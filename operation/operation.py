import asyncio
import collections
import discord
import inspect
import random
from discord import Role, Member
from functools import wraps
from typing import Optional, Union

import sans
from sans.api import Api, Dumps

from redbot.core import checks, commands, Config
from redbot.core.commands.requires import permissions_check
from redbot.core.utils.mod import get_audit_reason

from .update import menu, Update


COMMAND = "command"
OFFICER = "officer"
SOLDIER = "soldier"


_levels = (COMMAND, OFFICER, SOLDIER)


def requires(level: str):
    try:
        level = _levels.index(level)
    except ValueError as ve:
        raise ValueError(f"Unknown level: {level!r}") from ve

    async def predicate(ctx):
        if not ctx.guild:
            return False
        cog = ctx.bot.get_cog(Operation.__name__)
        if not cog:
            return False
        required_levels = [f"{l}_role" for l in _levels[level:]]
        config = await cog.config.guild(ctx.guild).all()
        for r in required_levels:
            role_id = config[r]
            if role_id:
                role = ctx.guild.get_role(role_id)
                if not role:
                    return False
                return ctx.author.top_role >= role
        return False

    return permissions_check(predicate)


class Operation(commands.Cog):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        """
        Guild: {
            Category: CategoryChannel
            Staging: VoiceChannel
            Teams: [
                {
                    Channel: TextChannel
                    Leader: Member  # make plural for shotgun ops... later
                    Soldiers: {Member...}
                }
            ]
            Blacklist: {Member...}
        }
        """
        self.operations = {}
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_guild(
            op_archive=None,
            op_category=None,
            invchannel=None,
            soldier_role=None,
            officer_role=None,
            command_role=None,
        )

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @checks.mod()
    async def inv(self, ctx, uses: int = 1):
        invchannel = await self.config.guild(ctx.guild).invchannel()
        invchannel = ctx.guild.get_channel(invchannel)
        if not invchannel:
            return
        invite = await invchannel.create_invite(
            max_age=0 if uses else 1,
            max_uses=uses,
            temporary=False,
            unique=True,
            reason=get_audit_reason(ctx.author),
        )
        await ctx.send(invite.url, delete_after=120)

    @inv.command(name="set")
    @checks.admin_or_permissions(manage_guild=True)
    async def _inv_set(self, ctx, *, invchannel: discord.TextChannel):
        await self.config.guild(ctx.guild).invchannel.set(invchannel.id)
        await ctx.tick()

    # __________ TRIGGER __________

    @commands.command(name="next", hidden=True)
    async def _next(self, ctx):
        await menu(ctx)

    # __________ HUNTER UMBRA __________

    @commands.group()
    @requires(COMMAND)
    async def opset(self, ctx):
        """
        Configure various op settings.

        You should probably leave this to Darc to handle.
        """
        if not ctx.invoked_subcommand:
            settings = await self.config.guild(ctx.guild).all()
            await ctx.send(
                "\n".join(
                    f"{level.title()}: {ctx.guild.get_role(settings[f'{level}_role'])}"
                    for level in _levels
                )
            )

    @opset.command()
    @checks.admin_or_permissions(administrator=True)
    async def command(self, ctx, *, role: Role):
        await self.config.guild(ctx.guild).command_role.set(role.id)
        await ctx.tick()

    @opset.command()
    async def officer(self, ctx, *, role: Role):
        await self.config.guild(ctx.guild).officer_role.set(role.id)
        await ctx.tick()

    @opset.command()
    async def soldier(self, ctx, *, role: Role):
        await self.config.guild(ctx.guild).soldier_role.set(role.id)
        await ctx.tick()

    @opset.command()
    async def category(self, ctx, *, category: discord.CategoryChannel):
        await self.config.guild(ctx.guild).op_category.set(category.id)
        await ctx.tick()

    @opset.command()
    async def archive(self, ctx, *, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).op_archive.set(channel.id)
        await ctx.tick()

    @commands.command(usage="[shotgun_teams] [team_leaders...] [joint_roles...]")
    @requires(OFFICER)
    async def start_update(
        self, ctx, shotgun: Optional[int] = None, *objects: Union[Role, Member]
    ):
        """
        Sets up an operation channel with the specified settings.

        Ask Darc how to use this because he didn't have the time to write helptext yet.
        """
        if shotgun:
            return await ctx.send("Shotgun ops are still in the works.")
        if ctx.guild in self.operations:
            return await ctx.send("An operation is already ongoing.")
        async with ctx.typing():
            op = {}
            self.operations[ctx.guild] = op
            guild_settings = await self.config.guild(ctx.guild).all()
            roles = {}
            highest_role = None
            for level in reversed(_levels):
                role_id = guild_settings.get(f"{level}_role")
                role = ctx.guild.get_role(role_id)
                if role:
                    highest_role = role
                roles[level] = highest_role
            default_roles = set(filter(bool, roles.values()))
            args: dict = {Role: default_roles.copy(), Member: set()}
            for obj in objects:
                args[type(obj)].add(obj)
            if args[Role] - default_roles:
                if highest_role > ctx.author.top_role:
                    return await ctx.send("Only Command can run joint operations.")
            if not args[Member]:
                args[Member].add(ctx.author)
            if shotgun and shotgun > 10:
                obj = ctx.guild.get_role(shotgun) or ctx.guild.get_member(shotgun)
                if not obj:
                    return await ctx.send(f"{shotgun} teams on a shotgun op seems a bit... much.")
                shotgun = None
                args[type(obj)].add(obj)
            if shotgun and len(args[Member]) > 1:
                return await ctx.send(
                    "Shotgun ops with multiple leaders? You should probably tell Darc how you envision this, "
                    "because I'm not sure what to do here."
                )
            args = {k: list(v) for k, v in args.items()}
            cat_overs = {
                ctx.guild.default_role: discord.PermissionOverwrite(
                    read_messages=False,
                    send_messages=False,
                    read_message_history=True,
                    add_reactions=False,
                    mention_everyone=False,
                    connect=False,
                    speak=False,
                ),
                highest_role: discord.PermissionOverwrite(
                    read_messages=True, mention_everyone=True
                ),
                ctx.me: discord.PermissionOverwrite(
                    read_messages=True,
                    send_messages=True,
                    connect=True,
                    move_members=True,
                    mention_everyone=True,
                ),
            }
            staging_overs = cat_overs.copy()
            for role in args[Role]:
                # read_messages is View Channel
                staging_overs[role] = discord.PermissionOverwrite(read_messages=True, connect=True)
            op_overs = [cat_overs.copy() for i in range(len(args[Member]))]
            for i, member in enumerate(args[Member]):
                if not any(role in args[Role] for role in member.roles):
                    return await ctx.send(f"{member} doesn't have any of the required roles.")
                op_overs[i][member] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True, mention_everyone=True
                )
            reason = get_audit_reason(ctx.author, "Operation start.")
            cat = ctx.guild.get_channel(await self.config.guild(ctx.guild).op_category())
            if not cat:
                cat = await ctx.guild.create_category(
                    name="Operation", overwrites=cat_overs, reason=reason
                )
                await self.config.guild(ctx.guild).op_category.set(cat.id)
            op["category"] = cat
            channels = await asyncio.gather(
                *(
                    op["category"].create_text_channel(
                        name=f"team-{i}" if len(op_overs) > 1 else "operation",
                        overwrites=op_overs[i],
                        reason=reason,
                    )
                    for i in range(len(op_overs))
                )
            )
            op["teams"] = [
                {"leader": args[Member][i], "channel": channels[i]} for i in range(len(op_overs))
            ]
            staging = cat.voice_channels
            if not staging:
                staging = await cat.create_voice_channel(
                    name="CLICK TO JOIN", overwrites=staging_overs, reason=reason
                )
            else:
                staging = staging[-1]
                await staging.edit(name="CLICK TO JOIN", sync_permissions=False)
                await asyncio.gather(
                    *(
                        staging.set_permissions(k, overwrite=v, reason=reason)
                        for k, v in staging_overs.items()
                    )
                )
            op["staging"] = staging
        await ctx.send(f"Done. Remember to run `{ctx.prefix}update_over` once you're done.")
        # they aren't going to remember
        ctx.bot.loop.call_later(5 * 60 * 60, asyncio.ensure_future, ctx.invoke(self.update_over))

    @commands.command()
    @requires(OFFICER)
    async def update_over(self, ctx):
        """
        Marks an update as finished and removes access to operations channels.

        Since archiving is not yet complete, Darc will have to archive and delete the channel himself.
        """
        if ctx.guild not in self.operations:
            return
        # TODO: only op leaders / command
        # if ctx.author not in (t["leader"] for t in self.operations[ctx.guild]["teams"]):
        # return await ctx.send("Only op leaders can end ops")
        op = self.operations.pop(ctx.guild)
        reason = get_audit_reason(ctx.author, "Operation end.")
        # TODO: get update information
        # await asyncio.gather(*(channel.delete(reason=reason) for channel in op["category"].text_channels))

        m = []
        for channel in op["category"].text_channels:
            m.append(f"{channel.mention}: `{ctx.prefix}logsfrom {channel.id} {channel.mention}`")
            await channel.edit(sync_permissions=True)
        await ctx.bot.get_user(215640856839979008).send("\n".join(m))

        await op["category"].voice_channels[-1].edit(name="🚫", sync_permissions=True)
        await ctx.tick()
        # TODO: post update information

    @commands.command()
    @requires(OFFICER)
    async def opkick(self, ctx, *, member: Member):
        """
        Kicks the specified member from an ongoing op and prevents them from joining again.
        """
        if ctx.guild not in self.operations:
            return
        op = self.operations[ctx.guild]
        is_special = ctx.author == ctx.guild.owner or await ctx.bot.is_owner(ctx.author)
        if not is_special and member.top_role > ctx.author.top_role:
            return await ctx.send("You can't opkick higher ranks.")
        op.setdefault("blacklist", set()).add(member)
        for team in op["teams"]:
            team["soldiers"].discard(member)
            if member in team["channel"].overwrites:
                await team["channel"].set_permissions(member, None)
        await ctx.send(f"Member {member} has been removed from this op.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        if member.guild not in self.operations:
            return
        if before.channel == after.channel:
            return
        op = self.operations[member.guild]
        if after.channel != op["staging"]:
            return
        if member in op.get("blacklist", set()):
            return
        # assign member
        teams = op["teams"]
        if len(teams) == 1:
            team = teams[0]
        else:
            weights = [len(t.setdefault("soldiers", set())) for t in teams]
            m = max(weights)
            weights = [m - w for w in weights]
            team = random.choices(teams, weights)[0]
        team.setdefault("soldiers", set()).add(member)
        # assign permissions
        await asyncio.gather(
            team["channel"].set_permissions(member, read_messages=True, send_messages=True),
            op["staging"].set_permissions(member, read_messages=False, connect=False),
        )
        await team["channel"].send(f"{member.mention} has joined.")
        # GTFO
        await member.move_to(None)


"""
1. The leading officer would tell the bot that there is an op happening next update, along with what orgs are invited.
        (I) [p]do_update [roles...]
2. The bot creates a brand-new #staging channel open to only to officers. (I)
3. The bot opens the staging channel to all invited orgs (or just soldiers) when the op is one hour / half an hour away,
    and pings everyone with instructions to use .im_here or equivalent. (I)
        (II) [p]im_here | [p]im_not_here
4. The bot creates a brand-new #operation channel open only to the leading officer and those who have used .im_here.
    This could be made a read-only channel for HC, with write access being granted on using .im_here.
5. When update is over (either by checking the time or with an .update_over command):
        (III) [p]update_over
    a. the bot demasks everyone
    b. deletes the two channels
    c. and posts a summary in an officers-only #past-operations channel.
        This summary would include who participated (whoever used .im_here),
        what update it was, and (optionally) a file with logs for the deleted channels.
        Officers could provide more info if needed, like what regions were hit.
"""

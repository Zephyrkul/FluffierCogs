import asyncio
import collections
import discord
import inspect
import random
from discord import Role, Member
from functools import wraps
from io import BytesIO
from typing import Optional, Union

import sans
from sans.api import Api, Dumps

from redbot.core import checks, commands, Config
from redbot.core.commands.requires import permissions_check
from redbot.core.utils.mod import get_audit_reason
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import ReactionPredicate

from .update import menu, Update


MAX_FILE = 8_000_000
COMMAND = "command"
OFFICER = "officer"
SOLDIER = "soldier"


_levels = (COMMAND, OFFICER, SOLDIER)


def requires(level):
    try:
        if level:
            level = _levels.index(level)
        else:
            level = 0
    except ValueError as ve:
        raise ValueError(f"Unknown level: {level!r}") from ve

    async def predicate(ctx):
        if not ctx.guild:
            return False
        cog = ctx.bot.get_cog(Operation.__name__)
        if not cog:
            return False
        l_keys = [f"{l}_role" for l in _levels]
        cache = []
        config = await cog.config.guild(ctx.guild).all()
        for key in l_keys:
            role_id = config[key]
            role = ctx.guild.get_role(role_id)
            cache.append(role or role_id)
        cog.op_cache = cache
        return await _requires(ctx, level)

    return commands.check(predicate)


async def _requires(ctx, level):
    if not level:
        return True
    if await ctx.bot.is_owner(ctx.author):
        return True
    elif not isinstance(level, int):
        level = _levels.index(level)
    for requirement in ctx.cog.op_cache:
        if not requirement:
            continue
        elif isinstance(requirement, int):
            return False
        else:
            return ctx.author.top_role >= requirement
    return False


def message_format(message, last_message):
    final = []
    if not last_message:
        final.append(str(message.created_at.date()))
    elif message.created_at.date() != last_message.created_at.date():
        final.extend(("", message.created_at.date().isoformat()))
    if message.author.bot:
        author = f"BOT {message.author.display_name}"
    else:
        author = message.author.display_name
    if message.edited_at:
        if message.edited_at.date() == message.created_at.date():
            post = f" (edited {message.edited_at.time().isoformat('minutes')})"
        else:
            post = f" (edited {message.edited_at})"
    else:
        post = ""
    final.append(
        f"[{message.created_at.time().isoformat('minutes')}] {author}: {message.clean_content}{post}"
    )
    final.extend(attachment.url for attachment in message.attachments)
    return (f"{line}\n".encode("utf-8") for line in final)


async def log(team, destination):
    channel = team["channel"]
    bios = [BytesIO()]
    last_message = None
    members = set()
    async for message in channel.history(limit=None, oldest_first=True):
        bios[-1].writelines(message_format(message, last_message))
        if bios[-1].tell() > MAX_FILE:
            bios.append(BytesIO())
        members.add(message.author)
        last_message = message
    for bio in bios:
        bio.seek(0)
    if len(bios) == 1:
        bios = [discord.File(bios[-1], filename=f"{channel}.md")]
    else:
        bios = [discord.File(bio, filename=f"{channel}_part-{i}.md") for i, bio in enumerate(bios)]
    embed = (
        discord.Embed(
            title=str(channel).replace("-", " ").title(),
            description="\n".join(
                f"{m.top_role} {m.mention}{'*' if m not in members else ''}"
                for m in team["soldiers"]
            ),
            colour=team["leader"].colour,
        )
        .set_author(
            name=f"{team['leader'].top_role} {team['leader'].display_name} ({team['leader'].id})",
            icon_url=team["leader"].avatar_url,
        )
        .set_thumbnail(url=team["leader"].guild.icon_url)
    )
    if team["soldiers"] - members:
        embed.set_footer(text="*Member never spoke in the operation channel.")
    if not destination:
        destination = team["channel"]
    await destination.send(embed=embed)
    for bio in bios:
        await destination.send(file=bio)


class Operation(commands.Cog):
    def __init__(self, bot):
        super().__init__()
        self.op_cache = None
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
            **{f"{l}_role": None for l in _levels},
        )

    def cog_command_error(self, ctx, error):
        return asyncio.gather(
            ctx.send("https://imgur.com/Bv6GkIw"),
            ctx.bot.on_command_error(ctx, error, unhandled_by_cog=True),
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

        shotgun_teams: Unsupported. Do not use.
        team_leaders: Who leads each team. If nobody is specified, you are the one and only leader.
        joint_roles: Command only. Other roles which are permitted to join this op.
        """
        if shotgun:
            return await ctx.send("Shotgun ops are still in the works.")
        if ctx.guild in self.operations:
            return await ctx.send("An operation is already ongoing.")
        op: dict = {}
        self.operations[ctx.guild] = op
        async with ctx.typing():
            roles = {}
            highest_role = None
            for i, level in reversed(list(enumerate(_levels))):
                maybe_role = self.op_cache[i]
                if isinstance(maybe_role, Role):
                    highest_role = maybe_role
                roles[level] = highest_role
            default_roles = set(filter(bool, roles.values()))
            args: dict = {Role: default_roles.copy(), Member: set()}
            for obj in objects:
                args[type(obj)].add(obj)
            if args[Role] - default_roles:
                if not (await _requires(ctx, COMMAND)):
                    return await ctx.send("Your rank isn't high enough to run joint operations.")
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
            embed = (
                discord.Embed()
                .add_field(
                    name="Team Leaders",
                    value="\n".join(m.mention for m in args[Member]),
                    inline=False,
                )
                .add_field(
                    name="Joint Roles",
                    value="\n".join(r.mention for r in args[Role]),
                    inline=False,
                )
            )
            menu = await ctx.send(
                "Are you sure you want to run an op with these parameters?", embed=embed
            )
            start_adding_reactions(menu, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(message=menu, user=ctx.author)
            try:
                await ctx.bot.wait_for("reaction_add", check=pred, timeout=60)
            except asyncio.TimeoutError:
                pass
            if not pred.result:
                return await ctx.send(
                    "Alright, I've cancelled starting the op. Ask around if you're unsure how to use this."
                )
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
                staging_overs[member] = discord.PermissionOverwrite(
                    read_messages=False, connect=False
                )
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
            c = [
                (f"npa-coup-{i}" if random.random() < 0.001 else f"team-{i}")
                for i in range(len(op_overs))
            ]
            channels = await asyncio.gather(
                *(
                    op["category"].create_text_channel(
                        name=c[i] if len(op_overs) > 1 else c[i][: c[i].rindex("-")],
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

        The operation channels will be archived properly.
        """
        if ctx.guild not in self.operations:
            return
        if ctx.author not in (t["leader"] for t in self.operations[ctx.guild]["teams"]) and not (
            await _requires(ctx, COMMAND)
        ):
            return await ctx.send("Only op leaders and Command can end ops")
        op = self.operations.pop(ctx.guild)
        reason = get_audit_reason(ctx.author, "Operation end.")
        async with ctx.typing():
            archives = ctx.guild.get_channel(await self.config.guild(ctx.guild).op_archive())
            for team in op["teams"]:
                await log(team, archives)
                if archives:
                    await team["channel"].delete(reason=reason)
                else:
                    await team["channel"].edit(sync_permissions=True, reason=reason)
            await op["category"].voice_channels[-1].edit(
                name="🚫", sync_permissions=True, reason=reason
            )
        await ctx.tick()

    @commands.command(aliases=["opban"])
    @requires(OFFICER)
    async def opkick(self, ctx, *, member: Member):
        """
        Kicks or bans the specified member from an ongoing op.
        """
        if ctx.guild not in self.operations:
            return
        op = self.operations[ctx.guild]
        is_special = ctx.author == ctx.guild.owner or await ctx.bot.is_owner(ctx.author)
        if not is_special and member.top_role >= ctx.author.top_role:
            return await ctx.send(f"You can't {ctx.invoked_with} higher ranks.")
        if ctx.invoked_with == "opban":
            op.setdefault("blacklist", set()).add(member)
        for team in op["teams"]:
            if member == team["leader"]:
                return await ctx.send(
                    f"You can't {ctx.invoked_with} leaders. Use `{ctx.prefix}disband` instead."
                )
            if member not in team["soldiers"]:
                continue
            team["soldiers"].remove(member)
            await team["channel"].set_permissions(member, overwrite=None)
            if ctx.invoked_with == "opkick":
                await op["staging"].set_permissions(member, overwrite=None)
        await ctx.send(f"Member {member} has been removed from this op.")

    @commands.command()
    @requires(OFFICER)
    async def disband(self, ctx, *, leader: Member = None):
        """
        Disbands a team led by yourself or the specified leader.
        """
        if ctx.guild not in self.operations:
            return
        leader = leader or ctx.author
        if leader != ctx.author and not (await _requires(ctx, COMMAND)):
            return await ctx.send(f"Only Command can disband other teams.")
        # get leader's team
        teams = self.operations[ctx.guild]["teams"]
        if len(teams) == 1:
            return await ctx.send(
                f"There's only one team left. Use `{ctx.prefix}update_over` instead."
            )
        for i, team in enumerate(teams):
            if team["leader"] == leader:
                break
        else:
            return await ctx.send(f"No team found led by {leader}.")
        teams.pop(i)
        # distribute leader's team
        for member in team["soldiers"]:
            if len(teams) == 1:
                team = teams[0]
            else:
                weights = [len(t.setdefault("soldiers", set())) for t in teams]
                m = max(weights)
                weights = [m - w for w in weights]
                team = random.choices(teams, weights)[0]
            team.setdefault("soldiers", set()).add(member)
            overs = team["channel"].overwrites_for(member)
            overs.update(read_messages=True, send_messages=True)
            # assign permissions
            await team["channel"].set_permissions(member, overwrite=overs)
            await team["channel"].send(
                f"{member.mention} has joined from {leader.display_name}'s team."
            )
        # archive op channel
        archives = ctx.guild.get_channel(await self.config.guild(ctx.guild).op_archive())
        await log(team, archives)
        if archives:
            await team["channel"].delete()
        else:
            await team["channel"].edit(sync_permissions=True)
        await ctx.tick()

    @commands.command()
    async def im_not_here(self, ctx):
        if ctx.guild not in self.operations:
            return
        op = self.operations[ctx.guild]
        member = ctx.author
        for team in op["teams"]:
            if member == team["leader"]:
                return await ctx.send(
                    f"You can't {ctx.invoked_with} leaders. Use `{ctx.prefix}disband` instead."
                )
            if member not in team["soldiers"]:
                continue
            team["soldiers"].remove(member)
            await asyncio.gather(
                team["channel"].set_permissions(member, overwrite=None),
                op["staging"].set_permissions(member, overwrite=None),
            )
        await ctx.send(f"Member {member} has been removed from this op.")

    @commands.command()
    async def participants(self, ctx, *, leader: Member = None):
        if ctx.guild not in self.operations:
            return
        op = self.operations[ctx.guild]
        leader = leader or ctx.channel
        for team in op["teams"]:
            if leader in (team["leader"], team["channel"]):
                break
        else:
            return await ctx.send("I couldn't find the team you were trying to get info on.")
        embed = (
            discord.Embed()
            .add_field(name="Leader", value=team["leader"].mention, inline=False)
            .add_field(
                name="Soldiers", value="\n".join(m.mention for m in team["soldiers"]), inline=False
            )
        )
        await ctx.send(embed=embed)

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
        if member in op.setdefault("blacklist", set()):
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
        overs = team["channel"].overwrites_for(member)
        overs.update(read_messages=True, send_messages=True)
        # assign permissions
        await asyncio.gather(
            team["channel"].set_permissions(member, overwrite=overs),
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

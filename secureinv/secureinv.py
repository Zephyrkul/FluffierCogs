import asyncio
import discord
import logging

from redbot.core import commands, Config, checks
from redbot.core.utils.mod import get_audit_reason


LOG = logging.getLogger("red.secureinv")


class SecureInv(commands.Cog):
    def __init__(self, bot):
        super().__init__()
        self.invites = {}
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_guild(invite=None, welcome=None)
        self.config_cache = {}
        asyncio.ensure_future(self.get_invites(bot))

    async def get_invites(self, bot):
        settings = await self.config.all_guilds()
        self.config_cache = settings
        for guild in bot.guilds:
            if not guild.get_channel(settings.get(guild, {}).get("welcome")):
                continue
            try:
                self.invites[guild] = set(await guild.invites())
            except discord.Forbidden:
                self.invites[guild] = set()

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @checks.mod_or_permissions(create_instant_invite=True)
    async def inv(self, ctx, days: float = 0):
        inv = await self.config.guild(ctx.guild).invite()
        inv = ctx.guild.get_channel(inv)
        if not inv:
            return
        invite = await inv.create_invite(
            max_age=days * 86400,
            max_uses=0 if days else 1,
            temporary=True,
            unique=True,
            reason=get_audit_reason(ctx.author),
        )
        await ctx.send(invite.url, delete_after=120)

    @inv.group(name="set")
    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def _inv_set(self, ctx):
        if not ctx.invoked_subcommand:
            settings = await self.config.guild(ctx.guild).all()
            await ctx.send(
                "\n".join(f"{k.title()}: {ctx.guild.get_channel(v)}" for k, v in settings.items())
            )

    @_inv_set.command(name="channel")
    async def set_inv(self, ctx, *, invite: discord.TextChannel):
        if not invite.permissions_for(ctx.me).create_instant_invite:
            raise commands.BotMissingPermissions(["create_instant_invite"])
        await self.config.guild(ctx.guild).invite.set(invite.id)
        self.config_cache.set_default(ctx.guild.id, {})["invite"] = invite.id
        await ctx.tick()

    @_inv_set.command(name="welcome")
    async def set_welcome(self, ctx, *, welcome: discord.TextChannel):
        if (
            not welcome.permissions_for(ctx.me).embed_links
            or not welcome.permissions_for(ctx.me).manage_guild
        ):
            raise commands.BotMissingPermissions(["embed_links", "manage_guild"])
        await self.config.guild(ctx.guild).welcome.set(welcome.id)
        self.config_cache.setdefault(ctx.guild.id, {})["welcome"] = welcome.id
        if ctx.guild not in self.invites:
            self.invites[ctx.guild] = set(await ctx.guild.invites())
        await ctx.tick()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return
        guild = member.guild
        if not guild.me.guild_permissions.manage_guild:
            return
        if not guild.get_channel(self.config_cache.get(guild.id, {}).get("welcome")):
            return
        new_invites = await guild.invites()
        old_invites = self.invites.get(guild, set())
        if not old_invites:
            self.invites[guild] = new_invites
            return
        welcome_channel = guild.get_channel(await self.config.guild(guild).welcome())
        if not welcome_channel:
            return
        new_invites = self.invites[guild]
        revoked_invites = old_invites - new_invites
        invs = set()
        for inv in revoked_invites:
            if inv.max_uses - inv.uses == 1:
                invs.add(inv)
        for inv in new_invites:
            old_inv = discord.utils.get(old_invites, code=inv.code)
            if old_inv and old_inv.uses > inv.uses:
                invs.add(inv)
        if not invs:
            LOG.info(
                "No invite found for user %s (%s) in guild %s (%s)",
                member,
                member.id,
                guild,
                guild.id,
            )
            return
        elif len(invs) > 1:
            LOG.info(
                "Too many invites found for user %s (%s) in guild %s (%s)",
                member,
                member.id,
                guild,
                guild.id,
            )
            return
        inv = invs.pop()
        embed = discord.Embed(
            colour=member.guild.me.colour,
            timestamp=inv.created_at,
            description="This invite is not guaranteed to be correct. Use discretion when applying roles.",
        )
        embed.set_author(name=member, icon_url=member.avatar_url)
        embed.set_footer(text="Created At:")
        for attr in ("inviter", "max_age", "max_uses", "url"):
            embed.add_field(
                name=attr.replace("_", " ").title(), value=getattr(inv, attr) or "∞", inline=True
            )
        await welcome_channel.send(embed=embed)

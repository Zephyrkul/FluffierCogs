import asyncio
import discord
from datetime import timedelta

from redbot.core import commands, Config, checks
from redbot.core.utils.mod import get_audit_reason


class SecureInv(commands.Cog):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.last_purge = {}
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_guild(invite=None, purge=None)

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
            temporary=False,
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
            await ctx.send("\n".join(f"{k.title()}: {v}" for k, v in settings.items()))

    @_inv_set.command(name="channel")
    async def set_inv(self, ctx, *, invite: discord.TextChannel):
        if not invite.permissions_for(ctx.me).create_instant_invite:
            raise commands.BotMissingPermissions(["create_instant_invite"])
        await self.config.guild(ctx.guild).invite.set(invite.id)
        self.config_cache.set_default(ctx.guild.id, {})["invite"] = invite.id
        await ctx.tick()

    @_inv_set.command(name="purge")
    async def set_purge(self, ctx, days: float):
        if days <= 0:
            await self.config.guild(ctx.guild).purge.clear()
        else:
            await self.config.guild(ctx.guild).purge.set(days * 86400)
        await ctx.tick()

    @commands.Cog.listener()
    async def on_message(self, message):
        guild = message.guild
        last_purge = self.last_purge.get(guild)
        if last_purge and message.created_at < last_purge + timedelta(hours=1):
            return
        self.last_purge[guild] = message.created_at
        settings = await self.config.guild(guild).all()
        if not settings["purge"]:
            return
        invite = guild.get_channel(settings["invite"])
        if not invite:
            return
        for member in guild.members:
            if len(member.roles) > 1:
                continue
            delta = timedelta(seconds=settings["purge"])
            if member.joined_at < message.created_at - delta:
                await member.kick(reason="Automated purge for unroled users.")

import asyncio
import collections
import discord
import inspect
import pytz
import random
from contextlib import suppress
from dataclasses import dataclass, field, InitVar
from datetime import datetime, timedelta, timezone, tzinfo
from math import inf
from typing import ClassVar, Union

import sans
from sans.api import Api, Dumps

from redbot.core import checks, commands, Config
from redbot.core.utils.predicates import ReactionPredicate
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.mod import get_audit_reason


reactions = (
    "\N{LEFTWARDS BLACK ARROW}",
    "\N{WHITE HEAVY CHECK MARK}",
    "\N{CROSS MARK}",
    "\N{BLACK RIGHTWARDS ARROW}",
)


def BMP(**kwargs):  # because the constructer for this is dumb af
    return commands.BotMissingPermissions(tuple(kwargs.items()))


async def menu(ctx, update=None, timeout=30):
    perms = ctx.channel.permissions_for(ctx.me)
    if not perms.embed_links or not perms.add_reactions:
        raise BMP(embed_links=True, add_reactions=True)
    update = update or Update(ctx.message.created_at)
    message = await ctx.send(embed=update.embed(now=ctx.message.created_at))
    start_adding_reactions(message, reactions, loop=ctx.bot.loop)
    first = True
    while True:
        if first:
            first = False
        else:
            await message.edit(embed=update.embed(now=ctx.message.created_at))
        pred = ReactionPredicate.with_emojis(reactions, message, ctx.author)
        try:
            await ctx.bot.wait_for("reaction_add", check=pred, timeout=timeout)
        except asyncio.TimeoutError:
            with suppress(discord.HTTPException):
                await message.delete()
            return None
        if pred.result == 0:
            upd = update[-1]
            if upd.end.timestamp() >= ctx.message.created_at.timestamp():
                update = upd
        elif pred.result == 1:
            with suppress(discord.HTTPException):
                await message.clear_reactions()
            return update
        elif pred.result == 2:
            with suppress(discord.HTTPException):
                await message.delete()
            return None
        elif pred.result == 3:
            update = update[1]
        with suppress(discord.HTTPException):
            # pylint: disable=E1126
            await message.remove_reaction(reactions[pred.result], ctx.author)


@dataclass(order=True, frozen=True)
class Update:
    EASTERN: ClassVar[tzinfo] = pytz.timezone("US/Eastern")
    DAY: ClassVar[timedelta] = timedelta(days=1)
    END: ClassVar[timedelta] = timedelta(hours=2)

    dt: datetime = field(default_factory=datetime.utcnow)
    current: InitVar[bool] = False

    def __post_init__(self, current):
        # pylint: disable=no-member
        dt = self.dt
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=pytz.UTC)
        dte = dt.astimezone(self.EASTERN)
        if current:
            dte -= self.END
        zero = dte.hour < 12
        dt = dte.replace(hour=0 if zero else 12, minute=0, second=0, microsecond=0)
        while dt < dte:
            print(dt, dte, sep=" < ")
            if not zero:
                dt += self.DAY
            dt = dt.replace(hour=0 if zero else 12)
            zero = not zero
        super().__setattr__("dt", dt)

    def __getattr__(self, attr):
        return getattr(self.dt, attr)

    def __slice_gen(self, sl):
        sl = slice(
            0 if sl.start is None else sl.start,
            inf if sl.stop is None else sl.stop,
            1 if sl.step is None else sl.step,
        )
        i = sl.start
        while i < sl.stop:
            yield self[i]
            i += sl.step

    def __getitem__(self, key):
        if key == 0:
            return self
        if isinstance(key, slice):
            return self.__slice_gen(key)
        if isinstance(key, tuple):
            return (self[i] for i in key)
        days, off = divmod(key - 1, 2)
        hours = days * 24 + off * 12 + 6
        dt = self.dt + timedelta(hours=hours)
        print(days, off, hours, dt, sep=", ")
        return type(self)(dt)

    def __iter__(self):
        upd = self
        while True:
            yield upd
            upd = type(upd)(upd.dt + upd.TWELVE)

    def __reversed__(self):
        upd = self
        while True:
            yield upd
            upd = type(upd)(upd.dt - upd.TWELVE)

    @property
    def major(self):
        # pylint: disable=no-member
        return self.dt.hour == 0

    @property
    def minor(self):
        # pylint: disable=no-member
        return self.dt.hour == 12

    def embed(self, *, now=None):
        # pylint: disable=no-member
        dt = self.dt.astimezone(pytz.UTC)
        now = now or datetime.utcnow()
        now = now.astimezone(pytz.UTC)
        til = dt - now
        hours = til.days * 24 + til.seconds // 3600
        zero = timedelta(0)
        if til < zero:
            til = -til
            hours = til.days * 24 + til.seconds // 3600
            til = {
                "name": "Time Since",
                "value": f"Over {hours} hour{'' if hours == 1 else 's'} ago.",
            }
        elif til > self.END:
            hours = til.days * 24 + til.seconds // 3600
            til = {
                "name": "Time Until",
                "value": f"Over {hours} hour{'' if hours == 1 else 's'} from now.",
            }
        else:
            til = {"name": "Ongoing", "value": "This update is ongoing."}
        return (
            discord.Embed(color=discord.Color.from_hsv(random.random(), 1, 1), timestamp=dt)
            .add_field(name="UTC", value=f"{dt:%c}", inline=False)
            .add_field(**til, inline=False)
            .set_footer(text="Major" if self.major else "Minor")
        )

    @property
    def end(self):
        return self.dt + self.END


class Operation(commands.Cog):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.operations = {}
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_guild(invchannel=None)

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @checks.mod()
    async def inv(self, ctx, uses: int = 1):
        invchannel = await self.config.guild(ctx.guild).invchannel()
        invite = await ctx.guild.get_channel(invchannel).create_invite(
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

    @commands.command(name="next")
    async def _next(self, ctx):
        await menu(ctx)

import asyncio
import discord
import pytz
from calendar import timegm
from contextlib import suppress
from dataclasses import dataclass, field, InitVar
from datetime import datetime, timedelta, timezone, tzinfo
from math import inf
from typing import ClassVar

from redbot.core import commands
from redbot.core.utils.predicates import ReactionPredicate
from redbot.core.utils.menus import start_adding_reactions


def BMP(**kwargs):  # because the constructer for this is dumb af
    return commands.BotMissingPermissions(tuple(kwargs.items()))


reactions = (
    "\N{LEFTWARDS BLACK ARROW}",
    "\N{WHITE HEAVY CHECK MARK}",
    "\N{CROSS MARK}",
    "\N{BLACK RIGHTWARDS ARROW}",
)


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
    SIX: ClassVar[timedelta] = timedelta(hours=6)
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
        while dte.hour not in (0, 12) and (dte.minute, dte.second, dte.microsecond) != (0, 0, 0):
            zero = dte.hour < 12
            dte = dte.replace(hour=12 if zero else 0, minute=0, second=0, microsecond=0)
            if not zero:
                dte += self.DAY
            dte = dte.astimezone(self.EASTERN)
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
            upd = type(upd)(upd.dt + upd.SIX)

    def __reversed__(self):
        upd = self
        while True:
            yield upd
            upd = type(upd)(upd.dt - upd.SIX)

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

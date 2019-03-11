from .operation import Operation


def setup(bot):
    bot.add_cog(Operation(bot))

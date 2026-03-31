import datetime
import logging
import sys

import fluxer
import notion_client

from context import Context
from env import app

formatter = logging.Formatter("%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(formatter)
handler.setLevel(logging.DEBUG)
logger.addHandler(handler)

bot = fluxer.Bot(command_prefix="!", intents=fluxer.Intents.default())
notion = notion_client.AsyncClient(auth=app.notion_token)

context = Context(bot, notion)


@bot.event
async def on_ready():
    await context.update_guild_state(app.guild_id)
    if bot.user:
        logger.info(f"Bot is ready! {bot.user.username}")


@bot.event
async def on_voice_state_update(voice_state: fluxer.VoiceState):
    logger.info(voice_state)
    await context.register_voice_state(voice_state)


@bot.command()
async def ping(ctx):
    logger.info("ping received!")
    await ctx.reply("Pong!")


@bot.command()
@fluxer.checks.has_role(name=app.admin_group)
async def update_notion_groups(
    ctx: fluxer.Message,
):
    if ctx.guild is not None and ctx.guild.id == app.guild_id:
        await ctx.reply("creating roles and channels based in groups...")
        try:
            await context.create_student_channels(ctx.guild)
            await context.update_guild_state(ctx.guild.id)
            await ctx.reply("done!")
        except Exception as e:
            await ctx.reply(f"Cannot update groups, please check the logs :( ({e})")
            raise e
    else:
        await ctx.reply("please talk to me from a channel in the guild")


@bot.command()
@fluxer.checks.has_role(name=app.admin_group)
async def update_guild_state(
    ctx: fluxer.Message,
):
    if ctx.guild is not None and ctx.guild.id == app.guild_id:
        await ctx.reply("updating guild state...")
        try:
            await context.update_guild_state(app.guild_id)
            await ctx.reply("done!")
        except Exception as e:
            await ctx.reply(f"Cannot update groups, please check the logs :( ({e})")
            raise e
    else:
        await ctx.reply("please talk to me from a channel in the guild")


@bot.command()
@fluxer.checks.has_role(name=app.admin_group)
async def add_attendance(
    ctx: fluxer.Message, event_name: str, start_date: str, end_date: str
):
    if ctx.guild is not None and ctx.guild.id == app.guild_id:
        try:
            await context.register_attendance(
                event_name,
                datetime.datetime.fromisoformat(start_date),
                datetime.datetime.fromisoformat(end_date),
            )
            await ctx.reply("Attendance registered successfully!")
        except Exception as e:
            await ctx.reply(f"Cannot update groups, please check the logs :( ({e})")
            raise e
    else:
        await ctx.reply("please talk to me from a channel in the guild")


if __name__ == "__main__":
    bot.run(app.bot_token)

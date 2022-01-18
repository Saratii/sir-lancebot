import hashlib
import re
import string
from pathlib import Path
from typing import BinaryIO, Optional

import discord
from discord.ext import commands

from bot.bot import Bot

FORMATTED_CODE_REGEX = re.compile(
    r"(?P<delim>(?P<block>```)|``?)"        # code delimiter: 1-3 backticks; (?P=block) only matches if it's a block
    r"(?(block)(?:(?P<lang>[a-z]+)\n)?)"    # if we're in a block, match optional language (only letters plus newline)
    r"(?:[ \t]*\n)*"                        # any blank (empty or tabs/spaces only) lines before the code
    r"(?P<code>.*?)"                        # extract all code inside the markup
    r"\s*"                                  # any more whitespace before the end of the code markup
    r"(?P=delim)",                          # match the exact same delimiter from the start again
    re.DOTALL | re.IGNORECASE,              # "." also matches newlines, case insensitive
)

LATEX_API_URL = "https://rtex.probablyaweb.site/api/v2"
PASTEBIN_URL = "https://paste.pythondiscord.com"

THIS_DIR = Path(__file__).parent
CACHE_DIRECTORY = THIS_DIR / "cache"
CACHE_DIRECTORY.mkdir(exist_ok=True)
TEMPLATE = string.Template((THIS_DIR / "template.txt").read_text())


def _prepare_input(text: str) -> str:
    if match := FORMATTED_CODE_REGEX.match(text):
        return match.group("code")
    else:
        return text


class InvalidLatexError(Exception):
    """Represents an error caused by invalid latex."""

    def __init__(self, logs: str):
        super().__init__(logs)
        self.logs = logs


class Latex(commands.Cog):
    """Renders latex."""

    def __init__(self, bot: Bot):
        self.bot = bot

    async def _generate_image(self, query: str, out_file: BinaryIO) -> None:
        """Make an API request and save the generated image to cache."""
        payload = {"code": query, "format": "png"}
        async with self.bot.http_session.post(LATEX_API_URL, data=payload, raise_for_status=True) as response:
            response_json = await response.json()
        if response_json["status"] != "success":
            raise InvalidLatexError(logs=response_json["log"])
        async with self.bot.http_session.get(
            f"{LATEX_API_URL}/{response_json['filename']}",
            data=payload,
            raise_for_status=True
        ) as response:
            out_file.write(await response.read())

    async def _upload_to_pastebin(self, text: str) -> Optional[str]:
        """Uploads `text` to the paste service, returning the url if successful."""
        try:
            async with self.bot.http_session.post(
                PASTEBIN_URL + "/documents",
                data=text,
                raise_for_status=True
            ) as response:
                response_json = await response.json()
            if "key" in response_json:
                return f"{PASTEBIN_URL}/{response_json['key']}.txt?noredirect"
        except Exception:
            # 400 (Bad Request) means there are too many characters
            pass

    @commands.command()
    @commands.max_concurrency(1, commands.BucketType.guild, wait=True)
    async def latex(self, ctx: commands.Context, *, query: str) -> None:
        """Renders the text in latex and sends the image."""
        query = _prepare_input(query)
        query_hash = hashlib.md5(query.encode()).hexdigest()
        image_path = CACHE_DIRECTORY / f"{query_hash}.png"
        async with ctx.typing():
            if not image_path.exists():
                try:
                    with open(image_path, "wb") as out_file:
                        await self._generate_image(TEMPLATE.substitute(text=query), out_file)
                except InvalidLatexError as err:
                    logs_paste_url = await self._upload_to_pastebin(err.logs)
                    embed = discord.Embed(title="Failed to render input.")
                    if logs_paste_url:
                        embed.description = f"[View Logs]({logs_paste_url})"
                    else:
                        embed.description = "Couldn't upload logs."
                    await ctx.send(embed=embed)
                    image_path.unlink()
                    return
            await ctx.send(file=discord.File(image_path, "latex.png"))

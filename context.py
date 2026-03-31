import datetime
import logging
import sys

import fluxer
import notion_client
from fluxer import Permissions

from env import app

formatter = logging.Formatter("%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(formatter)
handler.setLevel(logging.DEBUG)
logger.addHandler(handler)


class Context:
    def __init__(self, bot: fluxer.Bot, notion: notion_client.AsyncClient):
        self.bot = bot
        self.notion = notion
        self.roles: dict[str, fluxer.Role] = {}
        self.channels: dict[str, fluxer.Channel] = {}
        self.members_by_username: dict[str, fluxer.GuildMember] = {}
        self.members_by_id: dict[int, fluxer.GuildMember] = {}
        self.student_assignments: dict[str, str] = {}
        self.student_notion_pages: dict[str, str] = {}
        self.student_voice_states: dict[str, fluxer.VoiceState] = {}

    async def update_guild_state(self, guild_id):
        guild = await self.bot.fetch_guild(guild_id)
        await self._get_roles(guild)
        await self._get_channels(guild)
        await self._get_members(guild)
        await self._get_student_assignments()

    async def _get_roles(self, guild: fluxer.Guild):
        logger.info("retrieving guild roles...")
        self.roles = {}
        num_roles = 0
        for role in await guild.fetch_roles():
            self.roles[role.name] = role
            num_roles += 1
        logger.info(f"roles cached: {num_roles}")

    async def _get_channels(self, guild: fluxer.Guild):
        logger.info("retrieving guild channels...")
        self.channels = {}
        if self.bot._http is not None:
            channels = await self.bot._http.get_guild_channels(guild.id)
            for channel_data in channels:
                self.channels[channel_data.get("name", "default")] = fluxer.Channel.from_data(
                    channel_data
                )
            logger.info(f"channels cached: {len(self.channels)}")

    async def _get_members(self, guild: fluxer.Guild):
        logger.info("retrieving guild members...")
        self.members = {}
        for member in await guild.fetch_members(limit=1000):
            username = f"{member.user.username}#{member.user.discriminator}"
            self.members_by_username[username] = member
            self.members_by_id[member.user.id] = member
        logger.info(f"members cached: {len(self.members_by_username)}")

    async def _get_student_assignments(self):
        logger.info("retrieving student assignments...")
        self.student_assignments = {}
        self.student_notion_pages = {}
        query_params = {
            "data_source_id": app.notion_members_datasource,
        }
        async for page in notion_client.helpers.async_iterate_paginated_api(
            self.notion.data_sources.query, **query_params
        ):
            properties = page.get("properties", {})
            try:
                username = (
                    properties.get("ID Fluxer")
                    .get("rich_text")[0]
                    .get("text")
                    .get("content")
                    .strip()
                )
            except (IndexError, TypeError) as e:
                logging.error(f"problems with username: {e}")
                username = None
            try:
                group = properties.get("Grupo").get("select").get("name").strip()
            except AttributeError as e:
                logging.error(f"problems with group: {e}")
                group = None
            if (
                username is not None
                and len(username) > 0
                and group is not None
                and len(group) > 0
            ):
                self.student_assignments[username] = group
                self.student_notion_pages[username] = page.get("id")
        logger.info(f"students cached: {len(self.student_assignments)}")

    async def _compute_attendance(
        self, start_date: datetime.datetime, end_date: datetime.datetime
    ) -> dict[str, int]:
        query_params = {
            "data_source_id": app.notion_voicestate_events_datasource,
            "filter": {
                "and": [
                    {
                        "property": "Fecha y Hora",
                        "date": {
                            "on_or_after": start_date.isoformat(),
                        },
                    },
                    {
                        "property": "Fecha y Hora",
                        "date": {
                            "on_or_before": end_date.isoformat(),
                        },
                    },
                ]
            },
            "sorts": [{"property": "Fecha y Hora", "direction": "ascending"}],
        }

        attendance_minutes: dict[str, int] = {}
        connection_start_dates: dict[str, datetime.datetime|None] = {}

        logger.info("computing attendance...")
        async for page in notion_client.helpers.async_iterate_paginated_api(
            self.notion.data_sources.query, **query_params
        ):
            properties = page.get("properties", {})
            try:
                event_actor = (
                    properties.get("Estudiante")
                    .get("title")[0]
                    .get("text")
                    .get("content")
                    .strip()
                )
            except (IndexError, TypeError) as e:
                logging.error(f"problems with event_actor: {e}")
                event_actor = None
                continue
            try:
                event_type = (
                    properties.get("Tipo de Evento").get("select").get("name").strip()
                )
            except AttributeError as e:
                logging.error(f"problems with event_type: {e}")
                event_type = None
            event_date = datetime.datetime.fromisoformat(
                properties.get("Fecha y Hora").get("date").get("start")
            )
            connection_start = connection_start_dates.get(event_actor, None)
            if (
                event_actor is not None
                and event_type is not None
                and event_date is not None
            ):
                if connection_start is None and event_type == "Conexión":
                    connection_start_dates[event_actor] = event_date

                elif connection_start is not None and event_type == "Desconexión":
                    attendance_minutes[event_actor] = (
                        attendance_minutes.get(event_actor, 0)
                        + round((event_date - connection_start).total_seconds() / 60)
                    )
                    connection_start_dates[event_actor] = None
        logger.info(
            f"attendance computed: {len(attendance_minutes)} people were connected at least for 1 minute"
        )
        return attendance_minutes

    async def register_attendance(
        self, event_name: str, start_date: datetime.datetime, end_date: datetime.datetime
    ):
        logger.info("adding event name to notion table...")
        # Add property if not exists
        update_params = {
            "data_source_id": app.notion_attendance_datasource,
            "properties": {event_name: {"number": {"format": "number"}}},
        }
        await self.notion.data_sources.update(**update_params)

        attendance = await self._compute_attendance(start_date, end_date)

        logger.info("updating attendance table...")
        # For each user
        for username, time in attendance.items():
            # define properties
            properties = {
                "ID Fluxer": {
                    "title": [{"type": "text", "text": {"content": username}}]
                },
                event_name: {"number": time},
            }
            student_page = self.student_notion_pages.get(username, None)
            if student_page is not None:
                logger.info(f"found student page: {student_page}")
                properties["Nombre Real"] = {"relation": [{"id": student_page}]}
            # get user page or create it if not exists
            query_params = {
                "data_source_id": app.notion_attendance_datasource,
                "filter": {
                    "and": [
                        {
                            "property": "ID Fluxer",
                            "rich_text": {"equals": username},
                        },
                    ]
                },
            }
            username_pages = await self.notion.data_sources.query(**query_params)
            if len(username_pages.get("results", [])) == 0:
                create_params = {
                    "parent": {"data_source_id": app.notion_attendance_datasource},
                    "properties": properties,
                }
                await self.notion.pages.create(**create_params)
            else:
                update_params = {
                    "page_id": username_pages.get("results")[0].get("id"),
                    "properties": properties,
                }
                await self.notion.pages.update(**update_params)
        logger.info(f"attendance table updated: {len(attendance)} updates")

    async def create_student_channels(self, guild: fluxer.Guild):
        logger.info("creating student channel categories (if they dont exist)...")
        student_role = await self.get_or_create_role(app.student_group, guild)
        admin_role = await self.get_or_create_role(app.admin_group, guild)
        student_group_permissions = (
            Permissions.VIEW_CHANNEL
            | Permissions.SEND_MESSAGES
            | Permissions.READ_MESSAGE_HISTORY
            | Permissions.ADD_REACTIONS
            | Permissions.ATTACH_FILES
            | Permissions.CONNECT
            | Permissions.SPEAK
            | Permissions.PIN_MESSAGES
            | Permissions.EMBED_LINKS
            | Permissions.STREAM
        )
        # create "chats-texto" category if not exists
        text_category = await self.get_or_create_channel(
            name="chats-texto", type=fluxer.ChannelType.GUILD_CATEGORY, guild=guild
        )
        # create "chats-voz" category if not exists
        voice_category = await self.get_or_create_channel(
            name="chats-voz", type=fluxer.ChannelType.GUILD_CATEGORY, guild=guild
        )

        logger.info("remove access to categories to students...")
        # remove all permissions for students to previous categories
        await self.add_channel_to_role(
            text_category, student_role, allow=None, deny=Permissions.VIEW_CHANNEL
        )
        await self.add_channel_to_role(
            text_category, admin_role, allow=Permissions.ADMINISTRATOR, deny=None
        )
        await self.add_channel_to_role(
            voice_category, student_role, allow=None, deny=Permissions.VIEW_CHANNEL
        )
        await self.add_channel_to_role(
            voice_category, admin_role, allow=Permissions.ADMINISTRATOR, deny=None
        )

        logger.info("creating roles for missing groups...")
        for student_username, student_group in self.student_assignments.items():
            # get student
            student = self.members_by_username.get(student_username)
            if student is None:
                logger.error(
                    f"student in list is none but it shouldn't: their name is {student_username} and their group is {student_group}"
                )
            elif student.has_role(student_role.id):
                # get their group role
                logger.info(f"configuring student {student_username}...")
                role = await self.get_or_create_role(student_group, guild)

                # remove other roles and add student to their roles (group and students)
                await student.edit(
                    roles=[role.id, student_role.id],
                    reason="CC5325 is updating the groups",
                )
                # get or create channels
                text_channel = await self.get_or_create_channel(
                    name=f"{role.name} (Texto)",
                    type=fluxer.ChannelType.GUILD_TEXT,
                    parent_id=text_category.id,
                    guild=guild,
                )
                voice_channel = await self.get_or_create_channel(
                    name=f"{role.name} (Voz)",
                    type=fluxer.ChannelType.GUILD_TEXT,
                    parent_id=voice_category.id,
                    voice_channel=True,
                    guild=guild,
                )
                await self.add_channel_to_role(
                    text_channel, role, allow=student_group_permissions
                )
                await self.add_channel_to_role(
                    voice_channel, role, allow=student_group_permissions
                )
            else:
                logger.error(
                    f"student {student_username} is not recognized as having the student role ???"
                )

    async def add_channel_to_role(
        self,
        channel: fluxer.Channel,
        role: fluxer.Role,
        allow: Permissions | None = None,
        deny: Permissions | None = None,
    ):
        logger.info(f"ading role {role.name} to channel {channel.name}...")
        if self.bot._http is not None:
            await self.bot._http.edit_channel_permissions(
                channel_id=channel.id,
                overwrite_id=role.id,
                allow=allow,
                deny=deny,
                type=0,
            )

    async def get_or_create_role(self, name: str, guild: fluxer.Guild) -> fluxer.Role:
        if name not in self.roles:
            logger.info(f"role {name} does not exist, creating...")
            if self.bot._http is not None:
                data = await self.bot._http.create_guild_role(
                    guild_id=guild.id,
                    name=name,
                )
                self.roles[name] = fluxer.Role.from_data(data)
        return self.roles[name]

    async def get_or_create_channel(
        self,
        name: str,
        type: fluxer.ChannelType,
        guild: fluxer.Guild,
        voice_channel: bool = False,
        parent_id: int | None = None,
    ) -> fluxer.Channel:
        # Text created channels fix
        if name in self.channels and voice_channel is True and not self.channels[name].is_voice_channel:
            logger.info(f"channel {name} was text but it should have been voice, deleting it...")
            if self.bot._http is not None:
                await self.bot._http.delete_channel(self.channels[name].id)
                del self.channels[name]
        if name not in self.channels:
            logger.info(f"channel {name} does not exist, creating...")
            if self.bot._http is not None:
                data = await self.bot._http.create_guild_channel(
                    guild_id=guild.id, name=name, type=type, parent_id=parent_id, voice_channel=voice_channel
                )
                self.channels[name] = fluxer.Channel.from_data(data)
        return self.channels[name]

    async def add_group_to_student(
        self,
        role_name: str,
        member: fluxer.GuildMember,
        guild: fluxer.Guild,
        bot: fluxer.Bot,
    ):
        role = await self.get_or_create_role(role_name, guild)
        logger.info(
            f"adding role {role_name} to student {member.user.username}#{member.user.discriminator}..."
        )
        if bot._http is not None:
            await bot._http.add_guild_member_role(
                guild.id,
                member.user.id,
                role.id,
                reason="Automatic role assignation by cc5325bot",
            )

    async def register_voice_state(self, ctx: fluxer.VoiceState):
        user = self.members_by_id[ctx.user_id].user
        username = f"{user.username}#{user.discriminator}"
        previous_state = self.student_voice_states.get(username, None)
        if previous_state is None or previous_state.channel_id != ctx.channel_id:
            self.student_voice_states[username] = ctx
            if ctx.channel_id is None:
                await self.add_voice_event_to_notion(username, "Desconexión", 0)
            else:
                await self.add_voice_event_to_notion(
                    username, "Conexión", ctx.channel_id
                )

    async def add_voice_event_to_notion(
        self, username: str, event_type: str, channel_id: int
    ):
        logger.info("new event received!")
        await self.notion.pages.create(
            **{
                "parent": {
                    "type": "data_source_id",
                    "data_source_id": app.notion_voicestate_events_datasource,
                },
                "properties": {
                    "Estudiante": {
                        "title": [{"type": "text", "text": {"content": username}}]
                    },
                    "Tipo de Evento": {
                        "select": {"name": event_type},
                    },
                    "ID Canal conectado": {"number": channel_id},
                    "Fecha y Hora": {
                        "date": {"start": datetime.datetime.now().isoformat()},
                    },
                },
            }
        )
        logger.info("event saved!")

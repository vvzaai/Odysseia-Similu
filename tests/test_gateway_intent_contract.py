import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from similubot.bot import (
    SimiluBot,
    build_gateway_intents,
    disabled_text_command_prefix,
)
from similubot.ui.skip_vote_poll import SkipVotePoll, VoteResult


def test_gateway_intents_are_non_privileged():
    intents = build_gateway_intents()

    assert intents.guilds
    assert intents.voice_states
    assert intents.reactions
    assert not intents.members
    assert not intents.presences
    assert not intents.message_content
    assert not intents.messages


def test_text_command_prefix_is_disabled():
    assert disabled_text_command_prefix(MagicMock(), MagicMock()) == ()


@patch("similubot.bot.commands.Bot")
def test_bot_uses_gateway_intent_contract(mock_bot_class, tmp_path):
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "download.temp_dir": str(tmp_path),
        "music.enabled": True,
    }.get(key, default)
    mock_bot_class.return_value = MagicMock()

    SimiluBot(config)

    _, kwargs = mock_bot_class.call_args
    intents = kwargs["intents"]
    assert kwargs["command_prefix"] is disabled_text_command_prefix
    assert not intents.members
    assert not intents.presences
    assert not intents.message_content


def test_skip_vote_uses_raw_reaction_events():
    ctx = MagicMock()
    ctx.bot.user.id = 999

    member = MagicMock()
    member.id = 123
    member.display_name = "Voter"
    member.bot = False

    song = MagicMock()
    song.title = "Song"

    poll = SkipVotePoll(
        ctx=ctx,
        current_song=song,
        voice_channel_members=[member],
        threshold=1,
    )
    poll.is_active = True
    poll.vote_message = MagicMock()
    poll.vote_message.id = 456
    poll._update_poll_message = AsyncMock()

    event_names = []

    async def wait_for(event_name, check, timeout):
        event_names.append(event_name)
        payload = MagicMock()
        payload.message_id = 456
        payload.emoji = "✅"
        payload.user_id = 123
        assert check(payload)
        return payload

    ctx.bot.wait_for = wait_for

    result = asyncio.run(poll._monitor_votes())

    assert result == VoteResult.PASSED
    assert event_names == ["raw_reaction_add"]

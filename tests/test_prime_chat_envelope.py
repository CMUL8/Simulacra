"""Prime chat envelope parsing — Simulacra observes request, does not invent."""

from simulacra.demo.prime_hook import _parse_chat_envelope


def test_parse_build_request():
	turn = _parse_chat_envelope(
		'{"reply":"Ready when you are.","request":"build","title":"BJP Brief","subtitle":"report"}'
	)
	assert turn.reply and "Ready" in turn.reply
	assert turn.request == "build"
	assert turn.title == "BJP Brief"
	assert turn.config and turn.config.title == "BJP Brief"


def test_parse_iterate_with_brief():
	turn = _parse_chat_envelope(
		'{"reply":"Densifying.","request":"iterate","brief":"make denser KPI strip"}'
	)
	assert turn.request == "iterate"
	assert turn.brief == "make denser KPI strip"


def test_invalid_request_defaults_await():
	turn = _parse_chat_envelope('{"reply":"ok","request":"explode"}')
	assert turn.request == "await_user"


def test_prose_fallback():
	turn = _parse_chat_envelope("Just a plain reply without JSON")
	assert turn.reply == "Just a plain reply without JSON"
	assert turn.request == "await_user"

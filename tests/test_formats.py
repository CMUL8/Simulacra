from simulacra.demo.formats import (
	formats_catalog,
	get_format,
	infer_kind_from_prompt,
	normalize_kind,
	template_path,
)


def test_normalize_and_aliases():
	assert normalize_kind("slides") == "slides"
	assert normalize_kind("deck") == "slides"
	assert normalize_kind("one-pager") == "one_pager"
	assert normalize_kind("app") == "data_app"
	assert normalize_kind(None) == "data_app"


def test_infer_from_prompt():
	assert infer_kind_from_prompt("A board deck on vendor risk") == "slides"
	assert infer_kind_from_prompt("A shareable risk report") == "report"
	assert infer_kind_from_prompt("A one-page risk briefing") == "one_pager"
	assert infer_kind_from_prompt("vendor risk dashboard") == "data_app"


def test_templates_exist():
	for kind in ("data_app", "report", "slides", "one_pager"):
		root = template_path(kind)
		assert (root / "package.json").is_file(), kind
		assert (root / "src" / "App.tsx").is_file(), kind
		assert get_format(kind).skill_file


def test_catalog():
	cats = formats_catalog()
	assert len(cats) == 4
	assert {c["kind"] for c in cats} == {"data_app", "report", "slides", "one_pager"}

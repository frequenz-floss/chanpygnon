# License: MIT
# Copyright © 2023 Frequenz Energy-as-a-Service GmbH

"""This module defines macros for use in Markdown files."""

from typing import Any

import markdown as md
from griffe import ModulesCollection, Object
from markdown.extensions import toc
from mkdocs_macros import plugin as macros
from mkdocstrings_handlers.python.handler import PythonHandler

_CODE_ANNOTATION_MARKER: str = (
    r'<span class="md-annotation">'
    r'<span class="md-annotation__index" tabindex="-1">'
    r'<span data-md-annotation-id="1"></span>'
    r"</span>"
    r"</span>"
)


def _slugify(text: str) -> str:
    """Slugify a text.

    Args:
        text: The text to slugify.

    Returns:
        The slugified text.
    """
    return toc.slugify_unicode(text, "-")


def _hook_macros_plugin(env: macros.MacrosPlugin) -> None:
    """Integrate the `mkdocs-macros` plugin into `mkdocstrings`.

    This is a temporary workaround to make `mkdocs-macros` work with
    `mkdocstrings` until a proper `mkdocs-macros` *pluglet* is available. See
    https://github.com/mkdocstrings/mkdocstrings/issues/615 for details.

    Args:
        env: The environment to hook the plugin into.
    """
    # get mkdocstrings' Python handler
    python_handler = env.conf["plugins"]["mkdocstrings"].get_handler("python")

    # get the `update_env` method of the Python handler
    update_env = python_handler.update_env

    # override the `update_env` method of the Python handler
    def patched_update_env(markdown: md.Markdown, config: dict[str, Any]) -> None:
        update_env(markdown, config)

        # get the `convert_markdown` filter of the env
        convert_markdown = python_handler.env.filters["convert_markdown"]

        # build a chimera made of macros+mkdocstrings
        def render_convert(markdown: str, *args: Any, **kwargs: Any) -> Any:
            return convert_markdown(env.render(markdown), *args, **kwargs)

        # patch the filter
        python_handler.env.filters["convert_markdown"] = render_convert

    # patch the method
    python_handler.update_env = patched_update_env


def define_env(env: macros.MacrosPlugin) -> None:
    """Define the hook to create macro functions for use in Markdown.

    Args:
        env: The environment to define the macro functions in.
    """
    # A variable to easily show an example code annotation from mkdocs-material.
    # https://squidfunk.github.io/mkdocs-material/reference/code-blocks/#adding-annotations
    env.variables["code_annotation_marker"] = _CODE_ANNOTATION_MARKER

    python_handler = env.conf["plugins"]["mkdocstrings"].get_handler("python")
    assert isinstance(python_handler, PythonHandler)

    def _get_docstring(symbol: str) -> str:
        symbols = python_handler._modules_collection  # pylint: disable=protected-access
        assert isinstance(symbols, ModulesCollection)

        try:
            obj = symbols[symbol]
        except KeyError as exc:
            raise ValueError(f"Symbol {symbol!r} not found.") from exc
        assert isinstance(obj, Object)

        docstring = obj.docstring
        if not docstring:
            raise ValueError(f"Symbol {symbol!r} has no docstring.")

        return docstring.value

    # The decorator makes the function untyped
    @env.macro  # type: ignore[misc]
    def docstring_summary(symbol: str) -> str:
        """Get the summary of a Python symbol.

        Args:
            symbol: The fully qualified name of the Python symbol to get the summary of.

        Returns:
            The summary of the Python symbol.
        """
        docstring = _get_docstring(symbol)
        summary = docstring.splitlines(keepends=False)[0]
        return python_handler.do_convert_markdown(
            summary, heading_level=1, strip_paragraph=True
        )

    # This hook needs to be done at the end of the `define_env` function.
    _hook_macros_plugin(env)

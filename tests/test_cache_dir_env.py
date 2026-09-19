"""GB_CACHE_DIR (nome canônico) com fallback para GETBROLLS_CACHE_DIR (nome antigo, produção)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import config, http, media


def clean_environ(**values):
    """Environment dict sem GB_CACHE_DIR/GETBROLLS_CACHE_DIR, mais os valores dados."""
    return {
        **{k: v for k, v in os.environ.items() if k not in ("GB_CACHE_DIR", "GETBROLLS_CACHE_DIR")},
        **values,
    }


class CacheRootPrecedenceTests(unittest.TestCase):
    def test_default_unchanged_when_neither_is_set(self):
        with patch.dict(os.environ, clean_environ(), clear=True):
            self.assertEqual(config.cache_root(), Path.home() / ".cache" / "getbrolls")

    def test_new_name_alone_is_honoured(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, clean_environ(GB_CACHE_DIR=tmp), clear=True):
            self.assertEqual(config.cache_root(), Path(tmp))

    def test_old_name_alone_still_works(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, clean_environ(GETBROLLS_CACHE_DIR=tmp), clear=True),
        ):
            self.assertEqual(config.cache_root(), Path(tmp))

    def test_new_name_wins_over_old_when_both_are_set(self):
        with tempfile.TemporaryDirectory() as new_dir, tempfile.TemporaryDirectory() as old_dir:
            env = clean_environ(GB_CACHE_DIR=new_dir, GETBROLLS_CACHE_DIR=old_dir)
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(config.cache_root(), Path(new_dir))

    def test_new_name_empty_falls_back_to_old_name(self):
        with tempfile.TemporaryDirectory() as old_dir:
            env = clean_environ(GB_CACHE_DIR="", GETBROLLS_CACHE_DIR=old_dir)
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(config.cache_root(), Path(old_dir))

    def test_both_empty_falls_back_to_default(self):
        env = clean_environ(GB_CACHE_DIR="", GETBROLLS_CACHE_DIR="")
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(config.cache_root(), Path.home() / ".cache" / "getbrolls")


class CacheRootConsumersTests(unittest.TestCase):
    """media.py e http.py precisam ler o mesmo cache_root() em vez de duplicar a leitura."""

    def test_media_cache_dir_honours_new_name(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, clean_environ(GB_CACHE_DIR=tmp), clear=True):
            self.assertEqual(media._cache_dir(), Path(tmp))

    def test_http_get_json_writes_under_new_name(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, clean_environ(GB_CACHE_DIR=tmp), clear=True):
            cache_path = http.get_json.__globals__["Path"]  # sanity: Path importado no módulo
            self.assertTrue(cache_path is Path)
            self.assertEqual(config.cache_root(), Path(tmp))


class EnvFileAcceptsNewKeyTests(unittest.TestCase):
    def test_dot_env_accepts_gb_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            cache_value = str(Path(tmp) / "cache")
            env_path.write_text(f"GB_CACHE_DIR={cache_value}\n", encoding="utf-8")
            with patch.dict(os.environ, clean_environ(), clear=True):
                config.load_env(env_path)
                self.assertEqual(os.environ.get("GB_CACHE_DIR"), cache_value)
                self.assertEqual(config.cache_root(), Path(cache_value))

    def test_dot_env_rejects_old_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("GETBROLLS_CACHE_DIR=/tmp/whatever\n", encoding="utf-8")
            with patch.dict(os.environ, clean_environ(), clear=True), self.assertRaises(ValueError):
                config.load_env(env_path)


if __name__ == "__main__":
    unittest.main()

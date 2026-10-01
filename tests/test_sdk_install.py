"""`plugins install/update`: pasta ou git, dois passos, origem e commit no plugins.json.

Cobre também o endurecimento de segurança:

- Nunca fazer `checkout` de git; link simbólico recusado no conteúdo
  materializado, não na origem; `--yes` exige `--expect <sha256>` batendo com a
  prévia; manifesto validado e árvore limitada antes de copiar/clonar tudo;
  `plugins.json` corrompido recusa antes de qualquer mutação; a troca de pasta
  do `update` desfaz se a segunda metade falhar; `update` não liga de volta um
  plugin desabilitado; URL git com query/fragmento é recusada.
- Tamanho do blob checado (via `ls-tree -l`) antes de qualquer `cat-file`,
  inclusive na passada cedo do manifesto; TODA variável `GIT_*` é removida do
  ambiente do git, não só uma lista fixa, e o `GIT_SSH_COMMAND` de reserva (SSH
  em `BatchMode`) só entra sem override nenhum da pessoa; a varredura de pasta
  de resto não apaga a única cópia de um plugin e ignora pasta jovem demais pra
  ser resto de verdade; caminho com componente `.git` é recusado e uma colisão
  de nome (maiúsc./minúsc.) vira `ValueError` claro em vez de um `OSError` cru.
- Colisão de nome detectada direto da listagem do `ls-tree` (casefold), sem
  depender do disco de destino distinguir caixa — uma escrita real colidindo no
  disco passaria batido num disco case-sensitive (Linux/CI); também recusa alias
  de `.git` no Windows (ponto/espaço sobrando, nome curto 8.3 `GIT~1`); o epoch
  de criação vai no NOME da pasta de staging (`.install-<epoch>-<uuid>`,
  `.old-<epoch>-<id>-<uuid>`) em vez de `st_mtime` (que `os.replace`/
  `shutil.copytree` preservam do conteúdo de origem, fazendo uma pasta nova
  parecer velha), e nenhum erro de sistema de arquivos na varredura aborta
  quem chamou `install`/`update`; mais `GIT_SSL_CAINFO`/`GIT_SSL_CAPATH`/
  `GIT_SSH_VARIANT` preservados depois da limpeza de `GIT_*`, e
  `git_source.has_core_ssh_command` usa `--includes`.
"""

import atexit
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_loader import MANIFEST, PLUGIN_CODE, LoaderTestCase

from getbrolls import runtime
from getbrolls.sdk import git_source, loader
from getbrolls.sdk import install as install_mod

HAS_GIT = shutil.which("git") is not None


# MANIFEST/PLUGIN_CODE nunca são mutados; servem só de fixture padrão compartilhada.
def write_plugin(folder, manifest=MANIFEST, code=PLUGIN_CODE):  # pylint: disable=dangerous-default-value
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "getbrolls-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "plugin.py").write_text(code, encoding="utf-8")
    return folder


# O git dos TESTES (montar o repositório de origem) não pode herdar o config de
# quem roda a suíte: `commit.gpgsign=true` pediria senha/agente e um hook global
# poderia falhar ou travar o commit. Config global vazio, sem config de sistema,
# sem hooks e sem assinatura. O git do PRODUTO tem o próprio isolamento
# (`git_source.git_env`), que estes testes exercitam à parte.
_EMPTY_GITCONFIG_FD, _EMPTY_GITCONFIG = tempfile.mkstemp(prefix="gb-test-gitconfig-")
os.close(_EMPTY_GITCONFIG_FD)
atexit.register(lambda: Path(_EMPTY_GITCONFIG).unlink(missing_ok=True))
_GIT_ISOLATION = [
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "user.name=Teste",
    "-c",
    "user.email=teste@example.invalid",
]


def _git_test_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = _EMPTY_GITCONFIG
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def git(folder, *args):
    subprocess.run(
        ["git", *_GIT_ISOLATION, *args],
        cwd=folder,
        check=True,
        capture_output=True,
        env=_git_test_env(),
    )


def head(folder):
    done = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=folder, check=True, capture_output=True, text=True, env=_git_test_env()
    )
    return done.stdout.strip()


class InstallTestCase(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.work = Path(tempfile.mkdtemp(prefix="gb-src-"))
        # O Git para Windows grava objetos somente-leitura: `rmtree(ignore_errors=True)`
        # deixaria `.git`/staging para trás. `force_rmtree` libera a escrita e apaga.
        self.addCleanup(runtime.force_rmtree, self.work)
        self.addCleanup(runtime.force_rmtree, self.home)

    def env(self):
        return {"GB_HOME": str(self.home)}

    def state(self):
        return json.loads((self.home / "plugins.json").read_text(encoding="utf-8"))

    def leftover_staging(self):
        root = self.home / "plugins"
        if not root.exists():
            return []
        return [p.name for p in root.iterdir() if p.name.startswith((".install-", ".old-"))]


class FolderInstallTests(InstallTestCase):
    def test_install_from_a_folder_is_two_steps(self):
        source = write_plugin(self.work / "demo_src")
        # Bytecode na origem agora é recusado (test_sdk_pin_integrity); aqui só o caminho feliz.
        preview = run_cli("plugins", "--action", "install", "--source", source, env=self.env())
        self.assertFalse(preview["installed"])
        self.assertEqual("demo", preview["plugin"]["id"])
        self.assertEqual(["demo.example"], preview["plugin"]["permissions"]["network"])
        self.assertIsNone(preview["plugin"]["commit"])
        self.assertIsInstance(preview["plugin"]["sha256"], str)
        self.assertFalse((self.home / "plugins" / "demo").exists())
        self.assertEqual([], [p.name for p in (self.home / "plugins").iterdir()])

        done = run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            source,
            "--yes",
            "--expect",
            preview["plugin"]["sha256"],
            env=self.env(),
        )
        self.assertTrue(done["installed"])
        self.assertTrue((self.home / "plugins" / "demo" / "plugin.py").is_file())
        self.assertFalse((self.home / "plugins" / "demo" / "__pycache__").exists())
        state = self.state()
        self.assertEqual(done["plugin"]["sha256"], state["enabled"]["demo"]["sha256"])
        self.assertEqual({"source": str(source.resolve()), "commit": None}, state["sources"]["demo"])
        self.assertEqual("demo", run_cli("providers", env=self.env())["demo"]["plugin"])

    def test_already_installed_is_refused(self):
        source = write_plugin(self.work / "demo_src")
        preview = run_cli("plugins", "--action", "install", "--source", source, env=self.env())
        run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            source,
            "--yes",
            "--expect",
            preview["plugin"]["sha256"],
            env=self.env(),
        )
        err = run_cli("plugins", "--action", "install", "--source", source, expect=1, env=self.env())
        self.assertIn("--action update", err["error"])

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_symlink_in_the_source_is_refused(self):
        source = write_plugin(self.work / "demo_src")
        (source / "atalho.txt").symlink_to(Path(tempfile.gettempdir()))
        err = run_cli("plugins", "--action", "install", "--source", source, expect=1, env=self.env())
        self.assertIn("link simbólico", err["error"])
        self.assertFalse((self.home / "plugins" / "demo").exists())

    def test_bad_sources_are_refused_without_echoing_credentials(self):
        for source in (
            "nao/existe",
            "https://usuario:segredo@example.com/plugin.git",
            "ftp://example.com/p",
            "https://example.com/plugin.git?x=1",
            "https://example.com/plugin.git#frag",
            "git@example.com:org/repo.git?x=1",
        ):
            with self.subTest(source=source):
                err = run_cli("plugins", "--action", "install", "--source", source, expect=2, env=self.env())
                self.assertNotIn("segredo", json.dumps(err, ensure_ascii=False))
        run_cli("plugins", "--action", "install", expect=2, env=self.env())

    def test_update_needs_a_recorded_origin(self):
        self.install()
        err = run_cli("plugins", "--action", "update", "--id", "demo", expect=1, env=self.env())
        self.assertIn("plugins --action install", err["error"])

    def test_old_plugins_json_without_sources_is_still_valid(self):
        self.install()
        loader.enable("demo", confirm=True)
        self.assertNotIn("sources", self.state())
        self.assertEqual("enabled", loader.inventory()[0]["status"])
        (self.home / "plugins.json").write_text(
            json.dumps({"enabled": {}, "sources": {"demo": {"source": 1}}}), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            loader.read_state()

    # -- --yes exige --expect batendo com o sha256 da prévia -----------------

    def test_yes_needs_a_matching_expect(self):
        source = write_plugin(self.work / "demo_src")
        preview = run_cli("plugins", "--action", "install", "--source", source, env=self.env())
        sha = preview["plugin"]["sha256"]

        missing = run_cli("plugins", "--action", "install", "--source", source, "--yes", expect=2, env=self.env())
        self.assertIn("--expect", missing["error"])

        wrong = run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            source,
            "--yes",
            "--expect",
            "0" * 64,
            expect=1,
            env=self.env(),
        )
        self.assertIn("sha256", wrong["error"])
        self.assertFalse((self.home / "plugins" / "demo").exists())

        done = run_cli("plugins", "--action", "install", "--source", source, "--yes", "--expect", sha, env=self.env())
        self.assertTrue(done["installed"])

    # -- Manifesto validado e árvore limitada antes de copiar tudo ------------

    def test_install_refuses_a_source_over_the_file_cap(self):
        source = write_plugin(self.work / "demo_src")
        (source / "extra.txt").write_text("mais um arquivo\n", encoding="utf-8")
        with patch.object(install_mod, "MAX_FILES", 2), self.assertRaises(ValueError) as ctx:
            install_mod.install(str(source), confirm=False)
        self.assertIn("arquivos", str(ctx.exception))
        self.assertFalse((self.home / "plugins" / "demo").exists())

    # -- Plugins.json corrompido recusa antes de qualquer mutação ------------

    def test_install_refuses_up_front_when_plugins_json_is_corrupt(self):
        source = write_plugin(self.work / "demo_src")
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "plugins.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            install_mod.install(str(source), confirm=True, expect="0" * 64)
        plugins_dir = self.home / "plugins"
        self.assertEqual([], [p.name for p in plugins_dir.iterdir()] if plugins_dir.exists() else [])

    # -- Troca do update desfaz se a segunda metade falhar --------------------

    def test_update_restores_the_retired_folder_if_the_swap_fails(self):
        source = write_plugin(self.work / "demo_src")
        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])

        write_plugin(source, {**MANIFEST, "version": "0.2.0"}, PLUGIN_CODE + "\n# v0.2\n")
        preview2 = install_mod.update("demo", confirm=False)
        before = (self.home / "plugins" / "demo" / "plugin.py").read_text(encoding="utf-8")

        real_replace = os.replace
        fail_on_call = 2
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == fail_on_call:
                raise OSError("falha simulada no replace")
            return real_replace(src, dst)

        with patch("getbrolls.sdk.install.os.replace", side_effect=flaky), self.assertRaises(OSError):
            install_mod.update("demo", confirm=True, expect=preview2["plugin"]["sha256"])

        self.assertEqual(before, (self.home / "plugins" / "demo" / "plugin.py").read_text(encoding="utf-8"))
        self.assertEqual([], self.leftover_staging())

    def test_stale_staging_dirs_are_swept_at_the_start(self):
        source = write_plugin(self.work / "demo_src")
        (self.home / "plugins").mkdir(parents=True, exist_ok=True)
        # A idade vem do epoch codificado no NOME, não do
        # `st_mtime` — por isso o nome já nasce com um epoch antigo, sem
        # precisar de `os.utime`.
        old_epoch = int(time.time()) - (install_mod.STALE_STAGING_MAX_AGE_S + 60)
        stale_install = self.home / "plugins" / f".install-{old_epoch}-{uuid.uuid4().hex}"
        stale_install.mkdir()
        (stale_install / "resto.txt").write_text("x", encoding="utf-8")
        stale_old = self.home / "plugins" / f".old-{old_epoch}-outroplugin-{uuid.uuid4().hex}"
        # Vazio, sem manifesto: não é um plugin de verdade, então a varredura apaga (não restaura).
        stale_old.mkdir()

        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])

        self.assertFalse(stale_install.exists())
        self.assertFalse(stale_old.exists())

    # -- Update não liga de volta um plugin desabilitado ----------------------

    def test_update_keeps_a_disabled_plugin_disabled(self):
        source = write_plugin(self.work / "demo_src")
        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        loader.disable("demo")
        self.assertEqual("disabled", loader.inventory()[0]["status"])

        write_plugin(source, {**MANIFEST, "version": "0.2.0"}, PLUGIN_CODE + "\n# v0.2\n")
        preview2 = install_mod.update("demo", confirm=False)
        done = install_mod.update("demo", confirm=True, expect=preview2["plugin"]["sha256"])

        self.assertTrue(done["updated"])
        self.assertFalse(done["enabled"])
        self.assertNotIn("demo", self.state().get("enabled", {}))
        self.assertEqual("disabled", loader.inventory()[0]["status"])
        self.assertEqual("0.2.0", loader.inventory()[0]["version"])

    # -- TODA variável GIT_* é removida; SSH em BatchMode só sem override ----

    def test_git_env_strips_every_git_star_var(self):
        # Inclui as de sempre e as que vazam config/paths de outro
        # repositório sem serem um `GIT_DIR`/`GIT_WORK_TREE` explícito.
        poison = {
            "GIT_DIR": "/tmp/x",
            "GIT_WORK_TREE": "/tmp/y",
            "GIT_INDEX_FILE": "/tmp/z",
            "GIT_OBJECT_DIRECTORY": "/tmp/o",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/tmp/a",
            "GIT_CONFIG_PARAMETERS": "x",
            "GIT_COMMON_DIR": "/tmp/common",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_GLOBAL": "/tmp/global-gitconfig",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": "/tmp/hooks",
        }
        with patch.dict(os.environ, poison, clear=False):
            env = git_source.git_env(ssh=False)
        for key in poison:
            self.assertNotIn(key, env)
        self.assertEqual("0", env["GIT_TERMINAL_PROMPT"])
        self.assertEqual("1", env["GIT_CONFIG_NOSYSTEM"])
        self.assertEqual("1", env["GIT_LFS_SKIP_SMUDGE"])

    def test_ssh_batch_mode_only_applies_without_any_override(self):
        # Sem GIT_SSH_COMMAND/GIT_SSH e sem core.sshCommand configurado: usa o
        # BatchMode de reserva (isolado de `~/.gitconfig` de verdade via o patch
        # de `git_source.has_core_ssh_command`, não do ambiente real de quem roda o teste).
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GIT_SSH_COMMAND", None)
            os.environ.pop("GIT_SSH", None)
            with patch.object(git_source, "has_core_ssh_command", return_value=False):
                env = git_source.git_env(ssh=True)
            self.assertEqual("ssh -o BatchMode=yes", env["GIT_SSH_COMMAND"])

            # core.sshCommand já configurado: não empurra BatchMode por cima.
            with patch.object(git_source, "has_core_ssh_command", return_value=True):
                env2 = git_source.git_env(ssh=True)
            self.assertNotIn("GIT_SSH_COMMAND", env2)

        # GIT_SSH_COMMAND da pessoa é preservado (não vira o BatchMode nosso).
        with patch.dict(os.environ, {"GIT_SSH_COMMAND": "custom-ssh"}, clear=False):
            os.environ.pop("GIT_SSH", None)
            with patch.object(git_source, "has_core_ssh_command", return_value=False):
                env3 = git_source.git_env(ssh=True)
            self.assertEqual("custom-ssh", env3["GIT_SSH_COMMAND"])

        # GIT_SSH (variável legada) da pessoa também é preservado.
        with patch.dict(os.environ, {"GIT_SSH": "legado-ssh"}, clear=False):
            os.environ.pop("GIT_SSH_COMMAND", None)
            with patch.object(git_source, "has_core_ssh_command", return_value=False):
                env4 = git_source.git_env(ssh=True)
            self.assertEqual("legado-ssh", env4["GIT_SSH"])
            self.assertNotIn("GIT_SSH_COMMAND", env4)

        # Fonte não é SSH (pasta local ou https://): nunca mexe em SSH.
        env5 = git_source.git_env(ssh=False)
        self.assertNotIn("GIT_SSH_COMMAND", env5)
        self.assertNotIn("GIT_SSH", env5)

    def test_safe_git_env_vars_survive_the_git_star_cleanup(self):
        # GIT_SSL_CAINFO/GIT_SSL_CAPATH não redirecionam o git pra
        # outro repositório/config — são a CA que um proxy corporativo ou registro
        # interno exige; removê-las sem repor quebraria um clone HTTPS legítimo.
        safe = {"GIT_SSL_CAINFO": "/etc/ssl/corp-ca.pem", "GIT_SSL_CAPATH": "/etc/ssl/corp-certs"}
        with patch.dict(os.environ, safe, clear=False):
            env = git_source.git_env(ssh=False)
        self.assertEqual("/etc/ssl/corp-ca.pem", env["GIT_SSL_CAINFO"])
        self.assertEqual("/etc/ssl/corp-certs", env["GIT_SSL_CAPATH"])

        # Sem elas no ambiente de quem chama, não inventamos valor nenhum.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GIT_SSL_CAINFO", None)
            os.environ.pop("GIT_SSL_CAPATH", None)
            env2 = git_source.git_env(ssh=False)
        self.assertNotIn("GIT_SSL_CAINFO", env2)
        self.assertNotIn("GIT_SSL_CAPATH", env2)

    def test_git_ssh_variant_only_survives_alongside_git_ssh(self):
        # GIT_SSH_VARIANT ("ssh" x "putty"/"plink") só descreve o cliente que
        # GIT_SSH aponta; sem GIT_SSH não há o que descrever, então não sobrevive
        # sozinho — mesmo que a pessoa tenha definido só ele por engano.
        with patch.dict(os.environ, {"GIT_SSH": "plink.exe", "GIT_SSH_VARIANT": "putty"}, clear=False):
            os.environ.pop("GIT_SSH_COMMAND", None)
            with patch.object(git_source, "has_core_ssh_command", return_value=False):
                env = git_source.git_env(ssh=True)
        self.assertEqual("plink.exe", env["GIT_SSH"])
        self.assertEqual("putty", env["GIT_SSH_VARIANT"])

        with patch.dict(os.environ, {"GIT_SSH_VARIANT": "putty"}, clear=False):
            os.environ.pop("GIT_SSH", None)
            os.environ.pop("GIT_SSH_COMMAND", None)
            with patch.object(git_source, "has_core_ssh_command", return_value=False):
                env2 = git_source.git_env(ssh=True)
        self.assertNotIn("GIT_SSH_VARIANT", env2)
        self.assertEqual("ssh -o BatchMode=yes", env2["GIT_SSH_COMMAND"])

    def test_has_core_ssh_command_queries_global_includes(self):
        recorded = {}
        real_run = subprocess.run

        def spy(args, **kwargs):
            recorded["args"] = args
            # Repassa kwargs tal como veio: fixar check aqui mudaria o comportamento
            # de quem chama subprocess.run sem check de propósito.
            return real_run(args, **kwargs)  # pylint: disable=subprocess-run-check

        with patch.object(git_source.subprocess, "run", side_effect=spy):
            git_source.has_core_ssh_command(dict(os.environ))
        self.assertIn("--global", recorded["args"])
        self.assertIn("--includes", recorded["args"])
        self.assertIn("core.sshCommand", recorded["args"])

    # -- Caminho com componente ".git" é recusado (checagem unitária) -------

    def test_refuse_git_path_component_is_case_insensitive(self):
        for bad in (
            "x/.git/evil",
            ".git/evil",
            "sub/.GIT/x",
            "a/b/.Git/c",
            # Variantes que o Windows normaliza pra ".git" de
            # verdade ao gravar em disco (ponto/espaço sobrando à direita), e o
            # nome curto 8.3 que o NTFS pode resolver como alias de ".git".
            "sub/.git./evil",
            "sub/.git /evil",
            "sub/.GIT. /evil",
            "sub/GIT~1/evil",
            "sub/git~1/evil",
        ):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                install_mod._refuse_git_path_component(bad)
        install_mod._refuse_git_path_component("normal/path/plugin.py")  # não levanta
        install_mod._refuse_git_path_component("gita/evil")  # prefixo, não é ".git": não levanta

    # -- Varredura de resto não apaga a única cópia de um plugin -------------

    def test_sweep_restores_the_old_folder_when_the_current_one_is_missing(self):
        source = write_plugin(self.work / "demo_src")
        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])

        current = self.home / "plugins" / "demo"
        old_epoch = int(time.time()) - (install_mod.STALE_STAGING_MAX_AGE_S + 60)
        retired = self.home / "plugins" / f".old-{old_epoch}-demo-{uuid.uuid4().hex}"
        current.rename(retired)  # simula a morte do processo entre as duas trocas do update

        install_mod._sweep_stale_staging()

        self.assertFalse(retired.exists())
        self.assertTrue((current / "plugin.py").is_file())

    def test_sweep_skips_a_recent_staging_dir(self):
        (self.home / "plugins").mkdir(parents=True, exist_ok=True)
        fresh_install = self.home / "plugins" / f".install-{int(time.time())}-{uuid.uuid4().hex}"
        fresh_install.mkdir()

        install_mod._sweep_stale_staging()

        self.assertTrue(fresh_install.exists())

    def test_young_old_dir_is_not_swept_even_if_its_content_mtime_is_old(self):
        # A idade vem só do epoch no NOME. Um `.old-*` recém-criado
        # (epoch de agora) tem que ficar intocado mesmo que o CONTEÚDO dentro dele
        # (e a própria pasta) carreguem um `st_mtime` antigo — exatamente o que
        # `os.replace`/`shutil.copytree` fariam com o conteúdo de um plugin
        # instalado há muito tempo. Provar isso é o que faltava: um bug que volte
        # a olhar pro `st_mtime` faria este teste falhar.
        (self.home / "plugins").mkdir(parents=True, exist_ok=True)
        fresh_epoch = int(time.time())
        young = self.home / "plugins" / f".old-{fresh_epoch}-demo-{uuid.uuid4().hex}"
        write_plugin(young)
        old_time = time.time() - (install_mod.STALE_STAGING_MAX_AGE_S + 3600)
        os.utime(young / "getbrolls-plugin.json", (old_time, old_time))
        os.utime(young / "plugin.py", (old_time, old_time))
        os.utime(young, (old_time, old_time))

        install_mod._sweep_stale_staging()

        self.assertTrue(young.exists())
        self.assertFalse((self.home / "plugins" / "demo").exists())

    def test_sweep_leaves_an_unrecognized_staging_name_alone(self):
        # Nome que não bate com `.install-<epoch>-<uuid>`/`.old-<epoch>-<id>-<uuid>`
        # (de uma versão anterior deste código, ou qualquer outra coisa) não tem
        # como ter a idade calculada com confiança — mais seguro não mexer do que
        # arriscar apagar algo que não é mais o que costumava ser.
        (self.home / "plugins").mkdir(parents=True, exist_ok=True)
        legacy = self.home / "plugins" / ".old-orfao"
        legacy.mkdir()

        install_mod._sweep_stale_staging()

        self.assertTrue(legacy.exists())

    def test_a_sweep_error_does_not_abort_install(self):
        source = write_plugin(self.work / "demo_src")
        (self.home / "plugins").mkdir(parents=True, exist_ok=True)
        old_epoch = int(time.time()) - (install_mod.STALE_STAGING_MAX_AGE_S + 60)
        stale = self.home / "plugins" / f".old-{old_epoch}-outroplugin-{uuid.uuid4().hex}"
        stale.mkdir()

        with patch.object(install_mod, "_sweep_one_stale_entry", side_effect=OSError("falha simulada na varredura")):
            preview = install_mod.install(str(source), confirm=False)
        self.assertFalse(preview["installed"])

        done = install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        self.assertTrue(done["installed"])


@unittest.skipUnless(HAS_GIT, "git required")
class GitInstallTests(InstallTestCase):
    def repo(self):
        folder = write_plugin(self.work / "demo_repo")
        git(folder, "init", "--quiet")
        git(folder, "add", ".")
        git(folder, "commit", "--quiet", "-m", "v0.1.0")
        return folder

    def test_install_from_a_local_git_repo_records_the_commit(self):
        repo = self.repo()
        (repo / "rascunho.py").write_text("não commitado\n", encoding="utf-8")
        preview = run_cli("plugins", "--action", "install", "--source", repo, env=self.env())
        done = run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            repo,
            "--yes",
            "--expect",
            preview["plugin"]["sha256"],
            env=self.env(),
        )
        installed = self.home / "plugins" / "demo"
        self.assertEqual(head(repo), done["plugin"]["commit"])
        self.assertFalse((installed / ".git").exists())
        self.assertFalse((installed / "rascunho.py").exists())
        self.assertEqual(
            {"source": str(repo.resolve()), "commit": head(repo), "ref": None, "subdir": None},
            self.state()["sources"]["demo"],
        )

    def test_update_shows_the_diff_first_then_replaces_and_repins(self):
        repo = self.repo()
        preview0 = run_cli("plugins", "--action", "install", "--source", repo, env=self.env())
        run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            repo,
            "--yes",
            "--expect",
            preview0["plugin"]["sha256"],
            env=self.env(),
        )
        first_pin = self.state()["enabled"]["demo"]["sha256"]
        manifest = {**MANIFEST, "version": "0.2.0", "permissions": {"network": ["demo.example"], "env": []}}
        write_plugin(repo, manifest, PLUGIN_CODE + "\n# v0.2.0\n")
        (repo / "LEIAME.md").write_text("novo\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "v0.2.0")

        preview = run_cli("plugins", "--action", "update", "--id", "demo", env=self.env())
        self.assertFalse(preview["updated"])
        self.assertEqual({"from": "0.1.0", "to": "0.2.0"}, preview["diff"]["version"])
        self.assertEqual(["DEMO_TOKEN"], preview["diff"]["permissions"]["from"]["env"])
        self.assertEqual([], preview["diff"]["permissions"]["to"]["env"])
        self.assertEqual(["LEIAME.md"], preview["diff"]["files"]["added"])
        self.assertEqual(["getbrolls-plugin.json", "plugin.py"], preview["diff"]["files"]["changed"])
        self.assertIsInstance(preview["plugin"]["sha256"], str)
        self.assertEqual(first_pin, self.state()["enabled"]["demo"]["sha256"])
        self.assertEqual("0.1.0", loader.inventory()[0]["version"])

        done = run_cli(
            "plugins",
            "--action",
            "update",
            "--id",
            "demo",
            "--yes",
            "--expect",
            preview["plugin"]["sha256"],
            env=self.env(),
        )
        self.assertTrue(done["updated"])
        state = self.state()
        self.assertEqual("0.2.0", state["enabled"]["demo"]["version"])
        self.assertEqual(loader.folder_digest(self.home / "plugins" / "demo"), state["enabled"]["demo"]["sha256"])
        self.assertEqual(head(repo), state["sources"]["demo"]["commit"])
        self.assertEqual(["demo"], [p.name for p in (self.home / "plugins").iterdir()])
        self.assertEqual("enabled", loader.inventory()[0]["status"])

    # -- Filtro git (smudge) nunca roda, com ou sem --yes ---------------------

    def test_git_filter_smudge_never_runs(self):
        repo = self.repo()
        (repo / ".gitattributes").write_text("*.py filter=pwn\n", encoding="utf-8")
        git(repo, "add", ".gitattributes")
        git(repo, "commit", "--quiet", "-m", "gitattributes")

        # `git_source.git_env` remove TODA variável `GIT_*` — inclusive
        # `GIT_CONFIG_GLOBAL` — do que chega ao subprocesso git. Por isso o config
        # malicioso não pode ser injetado por essa variável (o próprio código a
        # apaga de propósito); em vez disso vai pelo `HOME`, que não é `GIT_*` e é
        # onde o git de verdade procura `~/.gitconfig` quando nada mais é dito.
        marker = self.work / "pwned.marker"
        fake_home = self.work / "fake-home"
        fake_home.mkdir()
        (fake_home / ".gitconfig").write_text(
            f'[filter "pwn"]\n\tsmudge = touch "{marker.as_posix()}" && cat\n\trequired = true\n',
            encoding="utf-8",
        )
        env = {**self.env(), "HOME": str(fake_home)}

        # Controle positivo: um clone comum (com checkout), com o mesmo HOME falso,
        # lê esse config e roda o filtro. Sem isto, "o marcador não existe" abaixo
        # passaria também com um config que o git nunca leu.
        plain_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        plain_env.update(HOME=str(fake_home), GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(
            ["git", "clone", "--quiet", "--", str(repo), str(self.work / "clone-comum")],
            env=plain_env,
            check=True,
            capture_output=True,
        )
        self.assertTrue(marker.exists(), "o clone comum devia rodar o smudge do config do HOME falso")
        marker.unlink()

        preview = run_cli("plugins", "--action", "install", "--source", repo, env=env)
        self.assertFalse(marker.exists())

        done = run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            repo,
            "--yes",
            "--expect",
            preview["plugin"]["sha256"],
            env=env,
        )
        self.assertTrue(done["installed"])
        self.assertFalse(marker.exists())
        self.assertEqual(PLUGIN_CODE, (self.home / "plugins" / "demo" / "plugin.py").read_text(encoding="utf-8"))

    # -- Link simbólico commitado e depois apagado do worktree local ---------

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_symlink_committed_then_removed_from_worktree_is_still_refused(self):
        repo = self.repo()
        link = repo / "atalho.py"
        link.symlink_to(repo / "plugin.py")
        git(repo, "add", "atalho.py")
        git(repo, "commit", "--quiet", "-m", "symlink")
        link.unlink()  # some do disco local, mas continua no histórico (HEAD) do repo

        err = run_cli("plugins", "--action", "install", "--source", repo, expect=1, env=self.env())
        self.assertIn("link simbólico", err["error"])
        self.assertFalse((self.home / "plugins" / "demo").exists())

    # -- git: árvore limitada antes de materializar tudo ---------------------

    def test_install_from_git_refuses_over_the_file_cap(self):
        repo = self.repo()
        with patch.object(install_mod, "MAX_FILES", 1), self.assertRaises(ValueError) as ctx:
            install_mod.install(str(repo), confirm=False)
        self.assertIn("arquivos", str(ctx.exception))

    # -- Tamanho do blob checado (via ls-tree -l) antes de qualquer cat-file -

    def test_git_oversized_blob_is_refused_without_reading_its_content(self):
        repo = self.repo()
        # Arquivo pequeno de verdade (poucos KB, disco continua minúsculo); só
        # precisa ficar maior que o teto rebaixado pelo teste.
        (repo / "grande.bin").write_bytes(b"x" * 50_000)
        git(repo, "add", "grande.bin")
        git(repo, "commit", "--quiet", "-m", "arquivo grande")
        big_sha = subprocess.run(
            ["git", "rev-parse", "HEAD:grande.bin"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

        real_blob = install_mod._git_blob

        def spy(dest, sha):
            if sha == big_sha:
                raise AssertionError("não devia ler o conteúdo do blob grande")
            return real_blob(dest, sha)

        with (
            patch.object(install_mod, "MAX_BYTES", 10_000),
            patch.object(install_mod, "_git_blob", side_effect=spy),
            self.assertRaises(ValueError) as ctx,
        ):
            install_mod.install(str(repo), confirm=False)
        self.assertIn("MB", str(ctx.exception))

    # -- Caminho ".git" e colisão de nome no histórico git -------------------

    def test_git_tree_name_collision_becomes_a_clear_value_error(self):
        # A colisão é detectada direto da listagem do `ls-tree`
        # (casefold dos caminhos), não da escrita real em disco — por isso este
        # teste vale em qualquer sistema de arquivos, incluindo um disco
        # case-sensitive (ubuntu-latest/CI Linux), onde a versão anterior deste
        # teste passava batido (as duas entradas simplesmente coexistiam).
        repo = self.repo()
        top_blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            input="topo\n",
        ).stdout.strip()
        nested_blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            input="aninhado\n",
        ).stdout.strip()
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"100644,{top_blob},Config"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"100644,{nested_blob},config/extra.py"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        git(repo, "commit", "--quiet", "-m", "colisao de nomes")

        err = run_cli("plugins", "--action", "install", "--source", repo, expect=1, env=self.env())
        self.assertIn("colidem", err["error"])
        self.assertFalse((self.home / "plugins" / "demo").exists())

    # -- URL git com query/fragmento é recusada -------------------------------

    def test_git_url_with_query_or_fragment_is_refused(self):
        for source in ("https://example.com/demo.git?token=segredo", "git@example.com:demo.git#ref"):
            with self.subTest(source=source):
                err = run_cli("plugins", "--action", "install", "--source", source, expect=2, env=self.env())
                self.assertIn("query", err["error"])


if __name__ == "__main__":
    unittest.main()

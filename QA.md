---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, recovery, qa]
---

# QA — pasta consolidada 2.3.4

## Estado atual

O fluxo próprio do autoedit foi reintegrado à pasta da skill. A afirmação anterior de que a 2.3.3 preservava o produto estava errada e foi retirada. Esta rodada não gera nem atualiza ZIP, conforme instrução do autor.

**Validação local: 52 testes passaram na fonte e os mesmos 52 passaram na cópia limpa instalada, em macOS/Python 3.14.6 com FFmpeg.** Instalação numa cópia limpa da pasta, sem autoedit instalado nela: yt-dlp/EJS pelo PyPI e Playwright CLI 0.1.20 pelo npm passaram. Nenhuma biblioteca externa faz parte do código distribuído; INSTALL orienta a instalação na máquina de cada pessoa.

## Evidência por capacidade

| Capacidade | Evidência | Limite |
|---|---|---|
| YouTube sem API key | Busca real, aquisição 0–3 s e GIF; nova instalação limpa repetiu trecho 3–5 s em 1920×1080/25fps | Um vídeo público; aprovação permaneceu pendente |
| Instagram pares | Captura no Chrome, curl dos dois canais, merge e decodificação integral reais; MP4 50,226 s, 1076×1912, H.264/AAC | Um Reel público; identidade dos canais conferida por asset e prévia |
| Instagram Chrome logado | Nova tentativa concluiu captura/CDN/merge e GIF de 5 s | Sessão do próprio usuário; sem copiar cookies para a skill |
| TikTok | Vídeo @nasa/7665358680627399966 baixado via instalação limpa; trecho 3 s, 576×1024/30fps, H.264/AAC; decodificação e GIF passaram | Link anterior indisponível no navegador; houve bloqueios/erros em tentativas anteriores |
| Pexels/Pixabay | Busca, refresh, download integral, GIF e corte técnico reais com chaves temporárias | Ensaio dos adapters na rodada anterior; sem aprovação editorial fictícia |
| Commons/NASA | Busca, refresh e HEAD de mídia reais na rodada anterior | Não equivalem a download integral de todos os itens |
| Prévia → coleta final | Fixture real FFmpeg, cache/hash/offset, aprovação sintética e corte verificado | Simulação explícita, não aprovação atribuída ao usuário |
| Instalação | venv + import yt-dlp/EJS; npm + Playwright --version; --check valida Node 22+ | Executáveis do sistema conforme INSTALL; não instala extensões/login |

## Regressões cobertas

- YouTube e fontes sociais têm transporte, sem exigir chave YouTube.
- URL Instagram pode conter perfil antes de `/reel/`.
- Preview remoto mantém origem, autor, aprovação pendente e intervalo original.
- Cache é reutilizado por hash e o fetch desconta `local_start_s`, preservando o trecho visto.
- Helpers próprios encontram yt-dlp local e habilitam Node; coletor não reutiliza parcial de download fracassado.
- Node antigo é rejeitado antes de instalar dependências.
- Arquivos de bibliotecas, fontes externas, mídia e credenciais não estão na seleção de distribuição.
- Permanecem testes de recuperação do ledger, revisão obsoleta, rejeição, interrupção, proporção, GIF, regras e logs sem segredos.

## Verificação documental

Revisor independente conferiu os transportes e apontou problemas de runtime, parcial curl, instalação e reuso de sessão. Corrigidos com regressões e documentação. O guia Instagram agora diferencia plugin do agente e extensão Playwright, mostra attach/tab-list/tab-select e captura por requests/response-body ou CDP. Não existe promessa de parser automático de captura: como no processo original, o agente opera o navegador e entrega pares ao coletor.

## Limites preservados

YouTube, Instagram e TikTok têm agora pelo menos um ensaio real concluído nesta rodada. Isso não garante qualquer URL/sessão: o primeiro link TikTok estava indisponível no site e o extrator retornou bloqueio em tentativas anteriores. Reteste com URL disponível pelo navegador e com as dependências instaladas pelo INSTALL. Não confundir esses erros com ausência de implementação.

macOS/Linux usam Bash e fcntl; Windows nativo não validado. Descoberta automática em novas sessões Codex/Claude e CI remoto não foram executados nesta rodada. O protótipo visual separado não foi incorporado automaticamente. A revisão usa fontes do sistema; sem fontes baixadas ou bibliotecas embutidas. Aprovação editorial continua humana.

Comandos para repetir na pasta da skill:

```sh
bash scripts/install.sh --check
bash scripts/install.sh
python3 -m unittest discover -s tests -v
python3 scripts/gb.py doctor
```

`doctor --live` executa buscas limitadas reais e pode consumir quota dos bancos configurados; não testa toda a captura Instagram/TikTok. Detalhes de origem em [API-STATUS](references/API-STATUS.md).

## Revisão final de documentação — 2.3.4

README, INSTALL, AGENTS, CHANGELOG, COMPATIBILITY, CONTRIBUTING, SECURITY e guias de navegação/revisão conferidos contra a implementação. AGENTS.md integra a seleção dos arquivos necessários à skill; `agents/openai.yaml` mantém a invocação `$get-brolls`.

Validação: 28 documentos com frontmatter e links relativos válidos; 13 exemplos de comandos principais aceitos pelo parser; scripts Bash com sintaxe válida. Revisão independente identificou e corrigiu dois pontos no README: `--project` obrigatório no exemplo local e distinção entre duração excedida (erro) e GIF acima do limite de bytes (fallback estático). Nenhum ZIP gerado/alterado. Essa revisão documental não altera a versão 2.3.4 nem substitui os ensaios ao vivo já registrados.

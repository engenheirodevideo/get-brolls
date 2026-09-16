---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-16
tags: [get-brolls]
---

# Segurança e privacidade

Não comite `.env`, originais, manifestos privados ou exports de revisão de clientes. Revise a árvore antes de publicar. O servidor de exemplo escuta apenas localhost. Não distribua `.venv/`, `.tools/`, perfis de navegador, cookies ou pares CDN assinados. Os instaladores obtêm as dependências nos registros oficiais.

Projetos/JSON são dados locais confiáveis. Não execute a skill como serviço público aceitando URLs, caminhos ou manifests arbitrários. O transporte HTTP interno de APIs/downloads resolve e valida todos os IPs antes da conexão, conecta aos IPs validados sem uma segunda resolução, mantém certificado/hostname HTTPS e bloqueia redirects. Ele não usa proxies automáticos do ambiente/sistema. Essa proteção não é um isolamento de rede de processos externos como yt-dlp, FFmpeg ou navegador.

A revisão não autentica quem clicou: importação exige atribuição humana com `--by`. O importador confere assinatura do conteúdo e versão da decisão (`reviewEpoch`) para impedir que exports antigos substituam decisões posteriores. Arquivos sem versão da decisão devem ser regenerados pelo Storyboard; não edite o JSON para contornar uma recusa.

## Para onde os dados vão

| Momento | Destinos | O que trafega |
|---|---|---|
| Instalação | PyPI (conjunto fixado em `requirements.txt`), registro npm (`package-lock.json`), Chrome/navegador opcional em passo separado | Apenas download das dependências. `npm ci --ignore-scripts` não baixa navegador nem executa scripts de pacote. |
| Execução | CDNs de YouTube, TikTok e Instagram via yt-dlp/curl; APIs de Pexels, Pixabay, Wikimedia Commons e NASA | URL solicitada, termos de busca e, quando existir, a chave do banco escolhido. |
| Nunca sai | Projetos, originais importados, JSON de revisão, chaves, `.env`, sessões e pares CDN assinados | Permanecem no disco local; nenhuma dessas informações é enviada a terceiros pela skill. |

Sem telemetria: a skill não envia dados a nenhum serviço próprio. Não há endpoint do autor, coleta de uso ou relatório automático de erro; todo tráfego sai para a fonte que você escolheu ou para os registros oficiais de dependências.

Para reportar vulnerabilidades, use o relatório privado do GitHub em **Security → Report a vulnerability**, habilitado neste repositório. Não publique segredos ou dados de clientes em issues públicas. Nenhum endereço de contato é presumido neste pacote.

# RenPySlim

> Ferramenta completa para reduzir o tamanho e empacotar recursos de jogos Ren'Py · Ren'Py asset slimming & packaging toolkit

**Idioma / Language:** [简体中文（默认）](README.md) | [English](README.en.md) | [Русский](README.ru.md) | [Español](README.es.md) | **Português (BR)** | [Türkçe](README.tr.md) | [Deutsch](README.de.md) | [Français](README.fr.md)

**Licença: [AGPL-3.0](LICENSE)** · Avisos de terceiros em [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

> Este projeto usa IA de forma intensiva; recomendamos que você verifique o código antes de usar. O desenvolvedor não se responsabiliza por quaisquer consequências causadas pelo uso incorreto. **Seus dados valem ouro!**

---

## O que é isso

O RenPySlim ajuda desenvolvedores de jogos Ren'Py a deixar suas obras **menores, mais organizadas e prontas para publicar**, tudo em um só fluxo:

- **Análise** — varre o projeto em busca de recursos grandes demais e gera um relatório com tamanho, problemas e recomendações
- **Compressão** — redução completa de imagens, áudio, vídeo e fontes, reescrevendo automaticamente as referências nos scripts;
  o padrão prioriza a qualidade (q95, quase sem perdas), e a otimização em paralelo aproveita todos os núcleos do processador
- **Empacotamento** — usa o SDK oficial para gerar pacotes de lançamento para PC / Mac / Android
- **Redução de obra pronta** — reduz com segurança um jogo já empacotado (pasta ou zip/7z/rar), entra e sai direto
- **Redução de APK** — o pacote Android também pode ser reduzido: imagens viram WebP, áudio vira OGG (remapeamento em tempo de execução, sem alterar as referências), com reassinatura automática
- **Desbloqueio por descompilação** (experimental) — para obras sem código-fonte, o unrpyc embutido recupera os scripts;
  imagens/áudio dentro dos pacotes também podem ser convertidos de formato e, depois, empacotados de volta nos pacotes RPA exatamente como antes

Junto vem um "check-up" completo do projeto em quatro itens: detecção de recursos sem uso,
limpeza de lixo antes de empacotar, detecção de arquivos duplicados e relatório de
caracteres faltando na fonte — e, após cada otimização, o lint oficial é executado automaticamente para validar.

**Seguro por padrão**: todas as operações primeiro copiam para uma cópia de trabalho, os originais ficam intactos; "se não ficou menor, não substitui";
recursos sem referência encontrada jamais são renomeados; cada execução gera um relatório de análise e uma lista de alterações.

## Início rápido

**Usuários comuns**: baixe o `RenPySlim.exe` na página de
[Releases](https://github.com/AxelBeary/renpyslim/releases), dê um clique duplo para executar e o navegador abre a interface automaticamente.

**Desenvolvedores**:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
python main.py            # 启动图形界面
```

## Interface gráfica (recomendado)

A interface tem layout com barra lateral, suporta **中文 / English / Русский / Español / Português (BR) / Türkçe / Deutsch / Français** e
**tema claro/escuro** (alterne no canto superior direito; se você não escolher manualmente,
ela segue o idioma do navegador e a aparência do sistema, e a sua escolha fica memorizada).
Quatro portas de entrada: **Super Empacotador / Redução de Obra Pronta / Redução de APK / Redução de Fonte**.

### Guia principal em quatro passos

1. Preencha o caminho (ou clique em "Navegar por arquivo compactado/Navegar por pasta" para abrir a caixa de seleção) e clique em "Escanear e analisar" → veja o relatório de análise
2. Marque as otimizações desejadas e escolha o nível de compressão
3. Clique em "Iniciar execução" → acompanhe a barra de progresso e os logs em tempo real
4. Ao final, receba o resultado otimizado / os pacotes oficiais de lançamento

### Operações práticas

- **Arraste um zip / 7z / rar / APK / pasta direto para o ícone da ferramenta**: o caminho é preenchido automaticamente e abre a função correspondente
- Se a ferramenta já estiver aberta e você arrastar um novo arquivo, ela abre uma nova aba para processá-lo, sem iniciar outra instância
- Os caminhos usados ficam guardados em "Usados recentemente", prontos com um clique

### As quatro portas de entrada

- **Super Empacotador**: aponte para a pasta do projeto; após a otimização, o SDK oficial é chamado para empacotar (PC/Mac/Android);
  é possível marcar "Empacotar recursos dentro de um pacote RPA" (canal oficial)
- **Redução de Obra Pronta**: aponte para a pasta da obra pronta ou simplesmente entregue um arquivo zip / 7z / rar (descompactação automática,
  redução e reempacotamento automático para entrega, com suporte a arquivos protegidos por senha); pacotes RPA encontrados são abertos, otimizados e reconstruídos automaticamente;
  se dentro do arquivo compactado houver um APK, ele é transferido automaticamente para a redução segura de APK; a opção experimental
  "Descompilar scripts para desbloquear conversão de formato", nas opções avançadas, permite que até obras sem código-fonte aproveitem a conversão de formato
- **Redução de APK**: escolha um arquivo .apk e resolva em três passos (nível / interruptor de redução máxima / assinatura entre três opções,
  com geração automática de uma nova chave por padrão), gerando um pacote reduzido pronto para instalar
- **Redução de Fonte** (ferramenta independente): não precisa de um projeto de jogo — basta escolher a fonte + a origem do texto para reduzir;
  coleções ttc/otc são separadas automaticamente e geradas por peso de fonte; nunca sobrescreve o original e inclui a lista de caracteres usados

### Garantias durante a execução

- Durante a execução você pode clicar em "Parar tarefa" a qualquer momento (o que já foi concluído é mantido); se a tarefa falhar, um despejo de erro é salvo automaticamente
- Quando houver versão nova, a interface avisa (comparando com o GitHub Releases)
- Se faltarem FFmpeg / 7-Zip, a interface mostra o método exato de instalação (comando winget ou endereço para download)
- Para sair: clique com o botão direito no ícone da bandeja (canto inferior direito) → Sair da ferramenta, ou use o botão "Sair da ferramenta" no canto inferior esquerdo da barra lateral
  (fechar a página do navegador não encerra a ferramenta)

## Modo headless (para scripts/automação, saída em JSON o tempo todo)

```
python cli.py env                                  # 环境体检
python cli.py analyze <路径> --mode project        # 分析
python cli.py optimize <路径> --preset balanced    # 优化
python cli.py full <工程路径> --platforms pc,mac   # 优化+打包一条龙
python cli.py slimfont <字体> <文本来源...>        # 独立字体瘦身
python cli.py slimapk <apk> --remap --gen-key      # APK 瘦身（图转WebP/音转OGG+重签名）
```

> Assistentes de IA / scripts de automação: leia primeiro o [AGENTS.md](AGENTS.md) (inclui regras de segurança e solução de erros).

## Requisitos do ambiente

| Dependência | Para que serve | Observações |
|---|---|---|
| Ren'Py SDK | Empacotamento, compilação dos scripts de remapeamento de APK | Geralmente é encontrado automaticamente; se não for, indique-o em "Configurações" na interface |
| FFmpeg | Otimização de áudio/vídeo | Pode estar no PATH ou na pasta bin ao lado do programa |
| Java/JDK | Empacotamento Android, reassinatura de APK | Na primeira vez, o empacotamento Android exige concluir antes a configuração do Android no lançador do Ren'Py |

O serviço da interface escuta por padrão em 127.0.0.1:52786 (porta pouco comum); se estiver ocupada,
usa automaticamente uma porta livre atribuída pelo sistema. Use a variável de ambiente `RENPYTOOLS_PORT` para especificar outra porta.

## Mecanismos de segurança

| Mecanismo | Descrição |
|---|---|
| Cópia de trabalho | Por padrão copia para uma cópia antes de mexer; nem um byte do original é alterado |
| Backup obrigatório | Ao marcar "Modificar os arquivos originais diretamente", primeiro é gerado um arquivo de backup completo (incluindo os saves) |
| Sem redução, sem substituição | Cada otimizador grava primeiro em arquivo temporário e só substitui quando o tamanho realmente diminui |
| Bloqueio por referência | Recursos sem referência literal nos scripts são comprimidos no lugar, nunca renomeados |
| Proteção das pastas do motor | Nos modos obra pronta/APK, renpy/, lib/ e assets/x-renpy/ nunca são tocados |
| Só marcar, nunca excluir | Arquivos suspeitos de não serem referenciados, por padrão, apenas entram no relatório; mesmo com a opção ativada, são movidos para a quarentena |
| Limpeza de lixo só exclui itens regeneráveis | Cache/logs/bytecode; no modo de modificar os originais diretamente, são ignorados automaticamente para proteger os saves |
| Imagens nunca são condenadas | O Ren'Py carrega imagens automaticamente pelo nome do arquivo; não achar a referência não significa que não é usada |
| Proteção contra entrada maliciosa | Desserialização com lista branca dos índices de pacotes; higienização dos caminhos das entradas de arquivos compactados (defesa contra zip-slip) |
| Somente local | O serviço escuta apenas em 127.0.0.1 e verifica a origem das requisições; inacessível pela internet |
| Lint automático após a otimização | A verificação estática oficial faz parte do fluxo, com saída arquivada em validation.txt |
| Lista de alterações | Cada execução gera o changelog.json, registrando todas as modificações |

## Fronteiras de segurança

- O serviço **escuta apenas em 127.0.0.1** (endereço "somente desta máquina"): outros dispositivos
  da rede local ou da internet simplesmente não conseguem estabelecer conexão; não é preciso configurar firewall,
  e não recomendamos expô-lo na internet de forma alguma;
- A ferramenta não oferece, nem pretende oferecer, uma opção de "abrir acesso pela rede"; se você modificar o código-fonte por conta própria,
  **desaconselhamos fortemente** alterar o endereço de escuta para 0.0.0.0 ou um endereço público — a interface não tem login/autenticação,
  e expô-la equivale a entregar a capacidade de ler e gravar arquivos desta máquina para qualquer pessoa que consiga acessá-la;
- A própria ferramenta não acessa a internet por iniciativa própria; a única exceção é "verificar novas versões" (comparando com o GitHub Releases;
  em caso de falha, é ignorada silenciosamente, sem afetar nenhuma funcionalidade).

## Testes

```
.venv\Scripts\python -m pytest tests -q
```

Cobre leitura/escrita de pacotes RPA (incluindo os dois formatos, antigo e novo, e bloqueio de pacotes maliciosos), segurança da reescrita de referências,
otimização de fontes/imagens sem corromper os arquivos originais, análise de rpyc, redução de APK (proteção do motor / remoção de assinatura /
conversão de caminhos com prefixo x- / geração de chaves), cancelamento e despejos de erro, padrões seguros, regressões de correções de auditoria,
proteção local do backend e integridade dos dicionários dos oito idiomas — 114 itens no total.

## Desenvolvimento

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller
python main.py            # 启动图形界面
build_exe.bat             # 重新打包 exe
```

**Mantenedores/Agentes, leiam primeiro:**

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): planta da arquitetura, linhas vermelhas de segurança, guia de extensão
- [docs/BACKLOG.md](docs/BACKLOG.md): arquivo de requisitos e pendências (novas ideias entram aqui primeiro)
- [docs/STATUS.md](docs/STATUS.md): estado da passagem de bastão e resultados de testes reais

## Suporte a vários idiomas / Localization

| Idioma | Interface | Documentação | Situação |
|---|---|---|---|
| 简体中文 | ✅ padrão | ✅ este documento | Disponível |
| English | ✅ | [README.en.md](README.en.md) | Disponível |
| Русский | ✅ | [README.ru.md](README.ru.md) | Disponível |
| Español | ✅ | [README.es.md](README.es.md) | Disponível |
| Português (BR) | ✅ | ✅ este documento | Disponível |
| Türkçe | ✅ | [README.tr.md](README.tr.md) | Disponível |
| Deutsch | ✅ | [README.de.md](README.de.md) | Disponível |
| Français | ✅ | [README.fr.md](README.fr.md) | Disponível |

Quer adicionar um novo idioma? Veja o "Guia de tradução" no [CONTRIBUTING.md](CONTRIBUTING.md) —
basta adicionar um dicionário à interface e um arquivo README.<código do idioma>.md à documentação.

## Licença e conformidade

- Este projeto é publicado sob a **AGPL-3.0**: você pode usar, modificar e distribuir este software livremente,
  mas versões modificadas (inclusive ao oferecer o serviço pela rede) devem ser abertas com a mesma licença.
  Reduzir o seu próprio jogo para uso pessoal não tem qualquer restrição; a obrigação de código aberto só se aplica ao distribuir versões modificadas.
- Declarações completas das dependências de terceiros e das implementações de referência de formatos:
  [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
  (inclui conformidade LGPL do pystray, agradecimentos ao formato Ren'Py e limites dos programas externos)
- Para contribuir, leia primeiro o [CONTRIBUTING.md](CONTRIBUTING.md);
  vulnerabilidades devem seguir o canal privado de relatório em [SECURITY.md](SECURITY.md).
- Ren'Py é marca registrada/projeto de Tom Rothamel e colaboradores; este projeto não tem afiliação com ele —
  é uma ferramenta independente de terceiros criada para a comunidade Ren'Py.

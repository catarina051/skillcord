# DIARIO — Skillcord

Registro de autoria: o que a Catarina decidiu e fez, e o que a IA fez. Uma entrada por sessão de trabalho.
Este diário começa em 2026-11-07; o histórico anterior está nos commits.

## 2026-11-07 · [PREENCHER] h
**Catarina decidiu:** só um aviso honesto de status no README, sem refazer nem expandir o projeto agora.
**Catarina fez:** [PREENCHER]
**A IA fez:** reexecutou as verificações do projeto com o ambiente virtual dele e escreveu o aviso de status no topo do `README.md`, em inglês como o resto do arquivo. Resultado medido em 2026-11-07 na branch `feat/runtime-v1`: 273 testes passando (7 ignorados), cobertura de 92,24% (mínimo exigido: 90%), `ruff check` e `mypy` sem erros. Na `main` pública (sem a pasta `cli`): 224 testes passando (7 ignorados), `ruff check` e `mypy` sem erros.
**Onde a IA errou e como foi corrigido:** o plano partia da auditoria de 31/10 ("227 de 231 testes, módulo de CLI não existe, erro de lint"). A IA não conseguiu reproduzir isso: a CLI de desenvolvimento (`skillcord-dev`) existe, mas só na branch local `feat/runtime-v1`, que está 11 commits à frente da versão pública e ainda não foi enviada; e não há erro de `ruff check` nem na `main` pública nem na branch. A IA também errou no caminho: rodou os testes com o Python global e depois com a pasta temporária do sistema, e viu 44 erros de coleta e de permissão que eram do ambiente, não do projeto; passou a usar o `.venv` do projeto e uma pasta temporária própria. O aviso de status foi escrito com o que se mediu, e não com o texto do plano ("CLI pendente"), que seria falso para a branch.
**Pendências para a Catarina decidir:** (1) enviar as 11 commits da branch ao GitHub, o que publica a CLI; (2) o `ruff format --check` acusa 34 arquivos para reformatar, o que não faz parte da CI nem do plano; só entra se ela quiser; (3) a linha "construído em N dias" do plano: pelos commits são 3 dias (11, 24 e 25 de agosto), e o README diz isso.
**Aprendizado:** [PREENCHER]

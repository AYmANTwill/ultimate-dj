# ECC — cheat-sheet pour UltimateDJ

Plugin installé : `ecc@ecc` v2.0.0 (user scope). Voir `claude plugin details ecc@ecc` pour l'inventaire complet. Ce document retient uniquement ce qui est pertinent pour la stack Python + PyTorch + librosa + CustomTkinter (Windows). Web/mobile/crypto/logistique/finance ignorés.

## Points d'attention

### 1. Coût token always-on ~22 163 tokens

À chaque session Claude Code, ECC injecte **~22 000 tokens** de contexte permanent avant que tu écrives quoi que ce soit. ~11 % d'une fenêtre 200k. Pour un throughput max ponctuel, `claude plugin disable ecc@ecc`.

### 2. 7 hooks qui s'exécutent AUTOMATIQUEMENT

| Hook | Effet visible |
|---|---|
| `SessionStart` | Charge rules + inventory |
| `PreToolUse` | **Fact-forcing gate** — exige déclaration avant chaque Bash / Write / rm -rf |
| `PostToolUse` | Trace usage |
| `PostToolUseFailure` | Diagnostic recovery |
| `PreCompact` | Sauvegarde avant compaction |
| `Stop`, `SessionEnd` | Persistance apprentissages |

Recovery si un hook bloque : `ECC_GATEGUARD=off` ou `ECC_DISABLED_HOOKS=pre:bash:gateguard-fact-force`.

## Top 10 composants ECC pour UltimateDJ

| # | Composant | Quand l'utiliser | Cible concrète |
|---|---|---|---|
| 1 | `ecc:python-reviewer` (agent) | Après toute modif `.py`. Mandate : "MUST BE USED for Python projects". | `Agent(subagent_type="ecc:python-reviewer", prompt="Review app/engine/transition_model.py — je viens de modifier bootstrap_pairs et le train loop")` |
| 2 | `ecc:pytorch-build-resolver` (agent) | Erreur tensor shape, CUDA OOM, load du `.pt` qui casse | `Agent(subagent_type="ecc:pytorch-build-resolver", prompt="load_state_dict() size mismatch on net.0.weight après bump feature_dim 134→140")` |
| 3 | `ecc:silent-failure-hunter` (agent) | Audit des 122 `except: pass` (voir docs/AUDIT.md) | `Agent(subagent_type="ecc:silent-failure-hunter", prompt="Audit app/engine/tracklists.py — flag data-path swallows vs légitimes progress callbacks")` |
| 4 | `ecc:refactor-cleaner` (agent) | Fichier explosé (settings.py 1959 LOC, tracklists.py 1234 LOC) | `Agent(subagent_type="ecc:refactor-cleaner", prompt="Split app/ui/settings.py into a package (item B4 in docs/AUDIT.md)")` |
| 5 | `ecc:security-reviewer` (agent) | Push public d'un patch auth/cookies/secrets | `Agent(subagent_type="ecc:security-reviewer", prompt="Audit sécurité tracklists.py login + secrets_store.py + _browser_launcher.py")` |
| 6 | `ecc:code-reviewer` (agent) | Review générique par commit | `Agent(subagent_type="ecc:code-reviewer", prompt="Review le dernier commit — respect CLAUDE.md conventions ?")` |
| 7 | `ecc:code-explorer` (agent) | Comprendre un module que tu n'as pas ouvert depuis longtemps | `Agent(subagent_type="ecc:code-explorer", prompt="Deep-dive segmentation.py + call sites — que casserait un _SR de 8000→16000 ?")` |
| 8 | `ecc:tdd-guide` (agent) | Nouvelle feature ou fix (coverage actuelle 3%) | `Agent(subagent_type="ecc:tdd-guide", prompt="TDD guide pour name_match_score() dans tracklists.py — fixture _tracklist_dump.html")` |
| 9 | `ecc:architect` (agent) | Feature ≥ 3 fichiers ou refactor structurel | `Agent(subagent_type="ecc:architect", prompt="Design le setlist.fm fallback (item C4 de docs/AUDIT.md)")` |
| 10 | `ecc:performance-optimizer` (agent) | Boot lent, DB query qui rame (cooc rebuild = 336s pour 55 tracklists) | `Agent(subagent_type="ecc:performance-optimizer", prompt="Profile match_with_library() — memoise _normalise, budget 10x speedup")` |

## Skills utiles (via /nom)

| Skill | Quand |
|---|---|
| `ecc:python-review` | avant push, double-check avec agent |
| `ecc:python-testing` | patterns pytest, fixtures, monkeypatch |
| `ecc:pytorch-patterns` | avant retravailler `transition_model.py` |
| `ecc:tdd-workflow` | pipeline TDD complet |
| `ecc:refactor-clean` | check heuristique avant refacto |
| `ecc:security-review` | mandate + checklist audit sécu |
| `ecc:codebase-onboarding` | à lancer si un futur contributeur rejoint |
| `ecc:git-workflow` | règles commits propres (rappel : PAS de Co-Authored-By: Claude ici) |

## Recipes (`/ecc-recipes <workflow>`)

```
/ecc-recipes fix a defect in the Siamese L4 training loop
→ orch-fix-defect (research → reproduce → TDD fix → review → commit)

/ecc-recipes rewrite the WAV repair function
→ python-test → python-build → python-review

/ecc-recipes plan the setlist.fm fallback and implement it
→ prp-prd → prp-plan → prp-implement → prp-commit → prp-pr
```

## Rules copiées (référence)

`~/.claude/rules/ecc/` :
- `common/` : agents, code-review, coding-style, development-workflow, git-workflow, hooks, patterns, performance, security, testing (10 fichiers)
- `python/` : coding-style, fastapi, hooks, patterns, security, testing (6 fichiers)

TypeScript, Go, Rust, Java, Swift, etc. non installés.

## Cheat-sheet ultra-rapide

| Situation | Composant ECC |
|---|---|
| Édité un `.py` | `ecc:python-reviewer` |
| PyTorch plante | `ecc:pytorch-build-resolver` |
| `except: pass` louche | `ecc:silent-failure-hunter` |
| Fichier > 700 LOC | `ecc:refactor-cleaner` / `ecc:code-simplifier` |
| Auth / cookies / secrets touché | `ecc:security-reviewer` |
| Nouvelle feature | `ecc:architect` puis `ecc:tdd-guide` |
| Module inconnu | `ecc:code-explorer` |
| Boot lent / query lente | `ecc:performance-optimizer` |
| Doute avant push | `ecc:code-reviewer` |
| Quelle séquence ? | `/ecc-recipes <description>` |
| Catalogue complet | `/ecc-guide` |

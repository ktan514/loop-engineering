# #89 Controlled E2E / Fault Gate matrix

製造Completion Gateは、実GitHub Repository / Projectを使うcontrolled E2Eと、同じproduction componentへ故障を注入するdeterministic Gateの両方で判定する。

| Gate | 実Production | Deterministic evidence |
| --- | --- | --- |
| Goal bootstrap / Issue / Project | `controlled-e2e.sh run` / `restart` | `test_bootstrap_repeated_run_converges_to_same_issues_and_items` |
| create effect直後の応答喪失 | restart run | `test_issue_create_response_failure_is_reconciled_by_marker_readback` |
| branch / PR重複抑止 | restart run | `test_new_branch_and_pr_are_created_once` |
| push後の応答喪失 | restart run | `test_push_failure_after_remote_effect_is_confirmed_by_readback` |
| PR create後の応答喪失 | restart run | `test_pr_create_failure_after_effect_is_confirmed_by_readback` |
| `UNCERTAIN`再送禁止 | restart run | `test_uncertain_branch_effect_is_not_resent`, `test_unknown_effect_becomes_uncertain_and_is_never_ready` |
| competing lineage | fault Gate | `test_competing_open_prs_are_blocked` |
| historical / HOLD PR誤採用禁止 | `historical/hold` draft PRを実repoに残してaudit | `test_competing_open_prs_are_blocked`とlineage branch identity検証 |
| stale CI | exact-head CI live readback | `test_ci_uses_only_exact_head` |
| stale review | exact-target External Review | `test_review_result_is_stale_when_head_moves_during_call` |
| duplicate review | External Review fresh pass | `test_same_exact_review_is_not_called_twice` |
| REQUEST_CHANGES → REPAIR | Local / External quality loop | `test_request_changes_returns_supervisor_to_repair`ほかLocal/External Review tests |
| CI failure → REPAIR | Supervisor / transition | `test_exact_head_ci_failure_returns_to_repair` |
| Human Verification exact HEAD | required Work policy | `test_human_verification_is_exact_head_bound`, `test_old_head_human_pass_is_not_current_pass` |
| process / DB restart | `controlled-e2e.sh restart` | `test_runtime_and_plan_survive_store_reconstruction` |
| draft merge防止 | live PR Ready → merge | `test_draft_merge_is_refused_without_ready_side_effect` |
| Goal completion | live Goal Issue / Project Done audit | Goal completion / autonomous runner tests |

## PASS条件

1. PR #107 exact-head deterministic CIがPASSする。
2. `./scripts/controlled-e2e.sh restart` が実環境で `CONTROLLED_E2E_AUDIT=PASS` まで到達する。
3. 実runで作成されたGoal/Work Issue、Project item、branch、PRに重複がない。
4. historical HOLD PRはdraftのまま残り、current lineageへ採用されない。
5. main exact HEAD上でfixture testsがPASSする。
6. deterministic fault Gateでblocking failureが0件。
7. fresh final reviewでblocking findingが0件。

上記が全て成立するまで#89および親#81をcompletedにしない。

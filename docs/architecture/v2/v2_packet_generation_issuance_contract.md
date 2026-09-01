# V2作業パケットgeneration明示発行契約

管理Issue: #67

関連正本: `v2_host_cutover_and_packet_execution.md`

状態: 製造仕様追補

## 1. 目的

`--issue-v2-packet`の再実行によって暗黙にgenerationが増え、操作者が意図しない新しい外部effectが発行されることを防ぐ。

## 2. generation Authority

作業パケット発行時はgenerationを明示指定する。

```text
--v2-generation <positive-integer>
```

HostはDBの最大generationから暗黙に`+1`して新しいpacketを作らない。

同じWork identityと同じgenerationから作るpacket identityは決定論的に同一とする。同じgeneration・同じ型付きeffect計画を再送した場合は、既存`ISSUED` packetと一致することを確認して冪等な「既発行」として扱える。

同じgenerationで異なるtransition、target、事前条件、期待effectを指定した場合は競合として拒否する。

新しいeffectを意図する場合だけ、操作者が新しいgenerationを明示する。

## 3. 完了条件

- generation省略ではpacket発行を開始しない。
- generationは1以上だけを許可する。
- 同一generationの別計画を上書きしない。
- 同一generationの再実行を理由に新generationを自動生成しない。
- `NO_EFFECT` / `UNCERTAIN`からの再試行でもgenerationを暗黙増加しない。

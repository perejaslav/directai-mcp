# v1.16.0 — запись ключевых целей (PriorityGoals) в `campaigns_update`

Дата: 2026-10-04. Репозиторий: `D:\github\directai-mcp`, ветка `main`
(локальный коммит, **не пушить**). Живые кампании не трогались: всё
проверено моками.

---

## 1. Сверка документации Яндекс Директ API

### 1.1 Где поддерживается `PriorityGoals`

| Тип кампании | `campaigns/update` | `campaigns/get` | Комментарий |
|---|---|---|---|
| `UNIFIED_CAMPAIGN` (ЕПК) | ✅ `UnifiedCampaign.PriorityGoals` | ✅ `UnifiedCampaignFieldEnum` | ЕПК работает только на `json/v501` |
| `TEXT_CAMPAIGN` | ✅ `TextCampaign.PriorityGoals` | ✅ `TextCampaignFieldEnum` | `v5` |
| `DYNAMIC_TEXT_CAMPAIGN` | ✅ `DynamicTextCampaign.PriorityGoals` | ✅ `DynamicTextCampaignFieldEnum` | DirectAI не разбирает этот тип (см. §5) |
| `SMART_CAMPAIGN` | ✅ `SmartCampaign.PriorityGoals` | ✅ `SmartCampaignFieldEnum` | то же |
| `MOBILE_APP_CAMPAIGN`, `CPM_BANNER_CAMPAIGN` | ❌ поля нет | ❌ | — |

Источники:

- документация, `update` для ЕПК:
  <https://yandex.ru/dev/direct/doc/ru/campaigns/update-unified-campaign.md>
- документация, `update` для текстово-графических:
  <https://yandex.ru/dev/direct/doc/ru/campaigns/update-text-campaign.md>
- документация, `update` (общий метод, ≤10 кампаний за вызов):
  <https://yandex.ru/dev/direct/doc/ru/campaigns/update.md>
- документация, `get` для ЕПК (`PriorityGoalsArray`):
  <https://yandex.ru/dev/direct/doc/ru/campaigns/get-unified-campaign.md>
- локальный WSDL репозитория (эталонная схема):
  `docs/api/v501/campaigns.wsdl` — `PriorityGoalsItem` (стр. 1963),
  `PriorityGoalsUpdateItem` (1970), `PriorityGoalsArray` (1978),
  `PriorityGoalsUpdateSetting` (1983), поля в
  `*UpdateItem` (2280/2294/2319/2343), в `*GetItem`
  (2379/2394/2420/2445); `OperationEnum` — `docs/api/v501/general.xsd:257`.

### 1.2 Формат

```json
"PriorityGoals": {          // PriorityGoalsUpdateSetting (update) / PriorityGoalsArray (get)
  "Items": [{               // PriorityGoalsUpdateItem (update) / PriorityGoalsItem (get)
    "GoalId": 201,          // long, обязательный; 12 = вовлечённые сессии
    "Value": 250000000,     // long, обязательный; валюта × 1 000 000 (250 ₽)
    "Operation": "SET",     // обязательный; сейчас доступно только SET
    "IsMetrikaSourceOfValue": "NO"   // необязательный; YES — только для CRR-стратегий
  }]
}
```

- `Items` при `update` — «**новый набор** ключевых целей и ценностей
  конверсий, которым нужно заменить существующий набор» (документация,
  `PriorityGoalsUpdateSetting`).
- `null (nil)` — удалить набор ключевых целей; автоматическая корректировка
  направляется на максимум вовлечённых сессий.
- `Value` — ценность конверсии; для `MaxProfit` — маржа с конверсии
  (тоже валюта × 1 000 000).
- `GoalId=13` («все приоритетные цели») в `BiddingStrategy.*.GoalId`
  допускам, если в `PriorityGoals` есть хотя бы одна цель, отличная от
  вовлечённых сессий.

### 1.3 Ограничения и совместимость

- `PackageBiddingStrategy` (пакетная стратегия) — при заполнении этого поля
  **нельзя** передавать `BiddingStrategy`, `PriorityGoals`, `CounterIds`,
  `AttributionModel`. Чтобы их передать, кампанию сначала отвязывают:
  `PackageBiddingStrategy: null` + новая `BiddingStrategy`.
- Для стратегий `AverageCpaMultipleGoals` / `PayForConversionMultipleGoals`
  нужен `PriorityGoals` **минимум с двумя** целями, где `Value` — целевая цена
  конверсии; `MaxProfit` требует `PriorityGoals` с маржой.
- `IsMetrikaSourceOfValue=YES` — только при `AVERAGE_CRR` /
  `PAY_FOR_CONVERSION_CRR`.
- В `campaigns/update` — не более 10 кампаний в вызове.

### 1.4 Расхождение с эталоном LidFly (04.10.2026)

| | Эталон LidFly | Реализация DirectAI |
|---|---|---|
| Поля в `Items` | `GoalId`, `Value`, `IsMetrikaSourceOfValue` | те же **+ `Operation: "SET"`** |
| Версия | `v501` для ЕПК | `v501` для ЕПК, `v5` для TEXT |

`Operation` — единственное расхождение: в эталоне поля нет, а в WSDL v501
(`docs/api/v501/campaigns.wsdl:1975`) у `PriorityGoalsUpdateItem` это
`minOccurs="1"`, и в документации оно обязательное («В настоящее время
доступно только значение SET»). Явный `SET` соответствует и схеме, и
документации, и по смыслу совпадает с эталоном (замена набора). Решение
зафиксировано в `DECISIONS.md` (v1.16.0). **Живой проверки не было** (задача
запрещает трогать живые кампании) — если сервер потребует иной набор полей,
это придёт ошибкой на `update`, а не молчаливым искажением целей.

---

## 2. Что сделано в коде

### 2.1 `src/directai_mcp/catalog/campaigns.py`

- Новая модель `PriorityGoal`: `goal_id: int > 0`, `value_rub: float > 0`,
  `is_metrika_source_of_value: bool = false` — с описаниями в JSON Schema
  (видно в `describe_action campaigns_update`).
- `_rub_to_micros()` — ₽ → `Value` через `Decimal(str(v)) * 1_000_000`
  (без float-ошибок; 0 микро-единиц — отказ).
- `CampaignsUpdateParams`:
  - `priority_goals: list[PriorityGoal] | None`,
  - `priority_goals_reset: bool = False`,
  - валидатор `mode="before"`: (1) набор и сброс вместе нельзя;
    (2) ключевые цели нельзя вместе с ключом пакетной стратегии
    (`package_strategy_id` и т. п. — по сырым параметрам, иначе `extra=ignore`
    проглотил бы их молча).
- `_prepare_campaigns_update`:
  - режим один — замена целиком: `{"Items": [{GoalId, Value, Operation: "SET",
    IsMetrikaSourceOfValue}]}`; сброс — `PriorityGoals: null` (JSON `null`);
  - пустой список без флага → отказ до API; повтор `goal_id`, `goal_id ≤ 0`,
    `value_rub ≤ 0` → отказ до API;
  - из уже существующего read кампании берутся `PriorityGoals` и
    `PackageBiddingStrategy` (без лишнего API-вызова); привязка к пакетной
    стратегии → отказ с инструкцией, как отвязать;
  - блок стратегии и `PriorityGoals` пишутся в один и тот же
    `UnifiedCampaign`/`TextCampaign`;
  - предпросмотр: `ключевые цели: <было> → <станет>` (ценности в ₽, имена целей
    и счётчики из `goals.toml`); предупреждение «Смена стратегии/целей
    сбрасывает обучение кампании.» (одно, если меняются и стратегия, и цели).
- `_verify_campaigns_updated` (read-back): запрашивает `PriorityGoals` и
  сверяет множество `(GoalId, Value, IsMetrikaSourceOfValue)` с запрошенным;
  при расхождении — тот же один повтор через 10 с; читает **той же версией**,
  что и запись (`v501` для ЕПК — иначе `v5` отдаёт ЕПК как `TEXT_CAMPAIGN`
  и цели читались бы не из того блока); в `note` попадает фактический набор
  целей после записи.
- Описание действия и ключи поиска дополнены («ключевые цели», «цели
  кампании», «ценность конверсии»).

### 2.2 `src/directai_mcp/safety/guard.py`

- `GOALS_BLOCK` / `GOALS_CONFIRM` — отдельные тексты (в `_BUDGET_KEYS` цели не
  добавлены: там блокировка про бюджет, и формулировка была бы ложной).
- `policy(...)` вызывается в `precheck` и `check_write` для `campaigns_update`
  с `priority_goals`/`priority_goals_reset`: `block` → запрет до API,
  `confirm` → «⚠ ОПАСНАЯ ОПЕРАЦИЯ» + `owner_confirmed=true`.
- `combat_allowed`: смена целей **не** разрешена в боевой кампании (учтён и
  сброс) → только `[TEST DirectAI]*`.
- Ослаблений нет: `plan_id`, TTL 15 минут, `acknowledge_warnings`,
  `owner_confirmed`, проверка живого имени кампании — как были.

### 2.3 `src/directai_mcp/server.py`

- В тексте инструкций MCP политика записи дополнена: к стратегии отнесены и
  ключевые цели с ценностями.

### 2.4 Чтение в `campaigns_get` (шаг 5 задачи)

Отдельного кода не потребовалось: `TEXT_FIELDS` и `UNIFIED_FIELDS` уже содержат
`PriorityGoals`, колонка `Goals` (и блок `- Цели:` при `full=true`) печатает
`Имя (GoalId, счётчик N): 500.00 ₽ (фикс|Метрика)`. Это ровно «все
поддерживаемые типы» — сервер разбирает `TEXT_CAMPAIGN` и `UNIFIED_CAMPAIGN`;
`DYNAMIC_TEXT_CAMPAIGN` и `SMART_CAMPAIGN` не разбираются (см. §5) и на
существующих тестах покрыты регрессией.

---

## 3. Тесты

`tests/test_priority_goals.py` — 22 теста, все моковые:

- сериализация: `₽ → микро` (500 → 500 000 000; 1234,56 → 1 234 560 000;
  0,1 + 0,2 = 300 000 без «половинки»), форма `Items`, отказ нулевой ценности,
  отказ `goal_id ≤ 0` / `value_rub ≤ 0`;
- валидация: дубли `goal_id`, пустой список без флага, набор+сброс вместе,
  конфликт с ключом `package_strategy_id`, отказ для кампании в пакетной
  стратегии;
- план: тело `UnifiedCampaign.PriorityGoals`, версия `v501` (ЕПК) и `v5`
  (TEXT), `before → after` в предпросмотре, сброс → `PriorityGoals: null`,
  стратегия и цели в одном плане (одно предупреждение);
- read-back: совпадение, расхождение (`N.priority_goals`), сброс;
- guard: `block` → «Заблокировано защитой: изменение ключевых целей кампании
  запрещено при защите.», `confirm` → опасная причина + отказ apply без
  `owner_confirmed`, боевая кампания по-прежнему не пишется;
- полный цикл `plan_write → apply_write(owner_confirmed, acknowledge_warnings)
  → read-back` → `статус applied`, повторный apply → «уже применён»;
- `campaigns_get`: ключевые цели в колонке `Goals` и в запрошенных
  `Text/UnifiedCampaignFieldNames`;
- поиск: «изменить ключевые цели кампании» → `campaigns_update`.

Прогон:

```
ruff check src tests        -> All checks passed!
pytest tests/test_priority_goals.py -> 22 passed
pytest (весь)               -> 723 passed, 4 failed
```

4 падения — до изменений, в `tests/test_v11_step2.py`
(`AllMockedAssertionError`: POST `json/v5/clients` не замокан); проверено на
чистом дереве через `git stash` — те же 4.

---

## 4. Документация, скилл, версия

- `README.md`: §5.2 «Ключевые цели (`campaigns_update`: `priority_goals`)» —
  формат, режим замены, сброс, запреты, read-back; правки §4 (таблица записи),
  §5 («Что можно где»), §6 (класс «стратегии», список причин `confirm`).
- `docs/PUBLIC-OVERVIEW.md`: guard — цели отнесены к стратегии.
- Skill `directai-workflow` (плагин `directai` для Codex): добавлен пункт
  «как менять ключевые цели» (форма, замена набора целиком, сначала прочитать
  текущие цели, сброс — флагом, пакетная стратегия, guard). Файл лежит вне
  репозитория, поэтому в коммит не входит.
- Версия: `pyproject.toml` и `src/directai_mcp/__init__.py` → **1.16.0**
  (`uv.lock` не трогали — в репозитории он остался на 1.12.1 и раньше).
- `CHANGELOG.md` (v1.16.0) и `DECISIONS.md` (v1.16.0 — про `Operation: SET`,
  флаг сброса, двойную проверку пакетной стратегии, класс «стратегии»).

---

## 5. Что сознательно не сделано

- `DYNAMIC_TEXT_CAMPAIGN` и `SMART_CAMPAIGN`: API их поддерживает, но DirectAI
  их не читает и не пишет (запись и раньше отклонялась как «неизвестный
  тип»). Расширение разбора типов кампаний — отдельная задача.
- Отвязка от пакетной стратегии (`PackageBiddingStrategy: null` + новая
  `BiddingStrategy`) не реализована: поэтому смена целей у такой кампании
  честно отклоняется с инструкцией.
- Проверки «стратегия требует ≥2 целей» для `*MultipleGoals`/`MaxProfit` в
  prepare нет: стратегия читается, но правило зависит от того, какая стратегия
  будет после записи; вместо блокировки — предупреждение о сбросе обучения.
  Если нужно — отдельный запрос.
- Живой проверки на реальной кампании не было (задача запрещает).

---

## 6. Коммит

Локальный, без пуша: см. `git log -1` (сообщение
`v1.16.0: запись ключевых целей (PriorityGoals)`). Метку `v1.16.0` на этом
коммите **не ставил** — по AGENTS.md §16 метки ставятся на релизном коммите
после мержа в `main` и пуша; решение за владельцем.
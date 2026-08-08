-- Схема CRM исполнения проектов для ООО «ПожСервис».
-- Проектный черновик к документу crm-target-design.md. В приложение НЕ подключён:
-- боевая схема живёт в infrastructure/db.py, сюда её переносить отдельным этапом.
--
-- Порядок разделов важен и проверен на копии боевой базы: сначала таблицы,
-- потом индексы, потом представления. Обратный порядок падает — индекс по
-- столбцу, который добавляется миграцией, не может быть создан раньше миграции.
--
-- COLLATE NOCASE_UNICODE — та же collation, что регистрирует приложение
-- (db.py:37). Без вызова conn.create_collation("NOCASE_UNICODE", ...) скрипт
-- не выполнится: это не опечатка, а соглашение проекта.
--
-- Проверено: выполняется на штатном sqlite3 целиком, без ошибок.


-- ═══════════════════════════════════════════════════════════════════
-- ТАБЛИЦЫ
-- ═══════════════════════════════════════════════════════════════════
-- ═══ CRM исполнения проектов ═══════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS workflow_state (
    entity      TEXT NOT NULL,
    code        TEXT NOT NULL,
    title       TEXT NOT NULL,
    is_final    INTEGER NOT NULL DEFAULT 0,
    is_waiting  INTEGER NOT NULL DEFAULT 0,
    sla_hours   INTEGER,
    sort_order  INTEGER NOT NULL DEFAULT 100,
    PRIMARY KEY (entity, code)
);

CREATE TABLE IF NOT EXISTS workflow_transition (
    entity     TEXT NOT NULL,
    from_code  TEXT NOT NULL,
    to_code    TEXT NOT NULL,
    action     TEXT NOT NULL DEFAULT '',
    role_key   TEXT NOT NULL DEFAULT '',
    need_note  INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 100,
    PRIMARY KEY (entity, from_code, to_code)
);

CREATE TABLE IF NOT EXISTS process_step (
    code         TEXT PRIMARY KEY,
    step_no      INTEGER NOT NULL,
    flow         TEXT NOT NULL DEFAULT '',
    lane         TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL,
    media_before TEXT NOT NULL DEFAULT '',
    match_entity TEXT NOT NULL DEFAULT '',
    match_state  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS event_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    happened_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_backdated INTEGER NOT NULL DEFAULT 0,
    actor        TEXT NOT NULL DEFAULT '',
    on_behalf_of TEXT NOT NULL DEFAULT '',
    entity       TEXT NOT NULL,
    entity_id    INTEGER NOT NULL,
    action       TEXT NOT NULL,
    from_state   TEXT NOT NULL DEFAULT '',
    to_state     TEXT NOT NULL DEFAULT '',
    project_id   INTEGER REFERENCES project(id) ON DELETE SET NULL,
    step_code    TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'trigger'
);

CREATE TABLE IF NOT EXISTS doc_counter (
    key          TEXT PRIMARY KEY,
    mask         TEXT NOT NULL,
    next_n       INTEGER NOT NULL DEFAULT 1,
    reset_period TEXT NOT NULL DEFAULT 'year',
    reset_mark   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS counterparty (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE_UNICODE,
    full_name   TEXT NOT NULL DEFAULT '',
    inn         TEXT NOT NULL DEFAULT '',
    kpp         TEXT NOT NULL DEFAULT '',
    is_customer INTEGER NOT NULL DEFAULT 0,
    is_supplier INTEGER NOT NULL DEFAULT 0,
    last_account TEXT NOT NULL DEFAULT '',
    last_bik     TEXT NOT NULL DEFAULT '',
    last_bank    TEXT NOT NULL DEFAULT '',
    code_1c     TEXT NOT NULL DEFAULT '',
    phone       TEXT NOT NULL DEFAULT '',
    email       TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contact_person (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    counterparty_id INTEGER REFERENCES counterparty(id) ON DELETE CASCADE,
    full_name       TEXT NOT NULL,
    position        TEXT NOT NULL DEFAULT '',
    phone           TEXT NOT NULL DEFAULT '',
    email           TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE COLLATE NOCASE_UNICODE,
    name            TEXT NOT NULL,
    org_id          INTEGER REFERENCES organization(id),
    customer_id     INTEGER REFERENCES counterparty(id),
    contract_number TEXT NOT NULL DEFAULT '',
    contract_date   TEXT,
    contract_amount_kop INTEGER,
    place_id        INTEGER REFERENCES place(id),
    object_address  TEXT NOT NULL DEFAULT '',
    date_start_plan TEXT,
    date_end_plan   TEXT,
    date_start_fact TEXT,
    date_end_fact   TEXT,
    pto_login       TEXT NOT NULL DEFAULT '',
    foreman_login   TEXT NOT NULL DEFAULT '',
    supply_login    TEXT NOT NULL DEFAULT '',
    curator_id      INTEGER REFERENCES contact_person(id),
    sign_days       INTEGER,
    status          TEXT NOT NULL DEFAULT 'draft',
    updated_by      TEXT NOT NULL DEFAULT '',
    updated_at      TEXT,
    status_seq          INTEGER NOT NULL DEFAULT 0,
    status_happened_at  TEXT,
    status_on_behalf_of TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS material (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE_UNICODE,
    unit       TEXT NOT NULL DEFAULT 'шт',
    article    TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'material',
    code_1c    TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS material_alias (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alias_norm  TEXT NOT NULL UNIQUE,
    alias_raw   TEXT NOT NULL DEFAULT '',
    material_id INTEGER NOT NULL REFERENCES material(id) ON DELETE CASCADE,
    source      TEXT NOT NULL DEFAULT 'user',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS estimate (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL DEFAULT 'customer',
    version        INTEGER NOT NULL DEFAULT 1,
    title          TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'draft',
    is_current     INTEGER NOT NULL DEFAULT 0,
    total_file_kop INTEGER,
    source_file    TEXT NOT NULL DEFAULT '',
    imported_at    TEXT,
    agreed_at      TEXT,
    agreed_by      TEXT NOT NULL DEFAULT '',
    agreed_with_id INTEGER REFERENCES contact_person(id),
    updated_by     TEXT NOT NULL DEFAULT '',
    updated_at     TEXT,
    status_seq          INTEGER NOT NULL DEFAULT 0,
    status_happened_at  TEXT,
    status_on_behalf_of TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, kind, version)
);

CREATE TABLE IF NOT EXISTS estimate_line (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    estimate_id  INTEGER NOT NULL REFERENCES estimate(id) ON DELETE CASCADE,
    parent_id    INTEGER REFERENCES estimate_line(id) ON DELETE CASCADE,
    prev_line_id INTEGER REFERENCES estimate_line(id) ON DELETE SET NULL,
    ord          INTEGER NOT NULL DEFAULT 0,
    pos_no       TEXT NOT NULL DEFAULT '',
    section      TEXT NOT NULL DEFAULT '',
    code         TEXT NOT NULL DEFAULT '',
    name         TEXT NOT NULL,
    unit         TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'work',
    qty_x1000    INTEGER NOT NULL DEFAULT 0,
    price_kop    INTEGER,
    amount_kop   INTEGER,
    material_id  INTEGER REFERENCES material(id),
    off_estimate INTEGER NOT NULL DEFAULT 0,
    version      INTEGER NOT NULL DEFAULT 1,
    notes        TEXT NOT NULL DEFAULT '',
    is_deleted   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS line_material (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id            INTEGER NOT NULL REFERENCES estimate_line(id) ON DELETE CASCADE,
    material_id        INTEGER NOT NULL REFERENCES material(id),
    qty_per_unit_x1000 INTEGER NOT NULL,
    waste_pct_x100     INTEGER NOT NULL DEFAULT 0,
    note               TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS price_quote (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER REFERENCES project(id) ON DELETE SET NULL,
    estimate_line_id INTEGER REFERENCES estimate_line(id) ON DELETE SET NULL,
    material_id      INTEGER REFERENCES material(id),
    name_text        TEXT NOT NULL DEFAULT '',
    supplier_id      INTEGER NOT NULL REFERENCES counterparty(id),
    qty_x1000        INTEGER,
    price_kop        INTEGER NOT NULL,
    quoted_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    valid_until      TEXT,
    lead_days        INTEGER,
    in_stock         INTEGER,
    source           TEXT NOT NULL DEFAULT 'phone',
    is_chosen        INTEGER NOT NULL DEFAULT 0,
    note             TEXT NOT NULL DEFAULT '',
    created_by       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS supply_request (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    number         TEXT NOT NULL DEFAULT '',
    need_by        TEXT,
    status         TEXT NOT NULL DEFAULT 'draft',
    assignee_login TEXT NOT NULL DEFAULT '',
    reject_reason  TEXT NOT NULL DEFAULT '',
    updated_by     TEXT NOT NULL DEFAULT '',
    updated_at     TEXT,
    status_seq          INTEGER NOT NULL DEFAULT 0,
    status_happened_at  TEXT,
    status_on_behalf_of TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS supply_request_line (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       INTEGER NOT NULL REFERENCES supply_request(id) ON DELETE CASCADE,
    estimate_line_id INTEGER REFERENCES estimate_line(id) ON DELETE SET NULL,
    material_id      INTEGER REFERENCES material(id),
    name_text        TEXT NOT NULL DEFAULT '',
    unit             TEXT NOT NULL DEFAULT '',
    qty_x1000        INTEGER NOT NULL DEFAULT 0,
    cancelled_at     TEXT,
    cancel_reason    TEXT NOT NULL DEFAULT '',
    note             TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS purchase_invoice (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER REFERENCES project(id) ON DELETE SET NULL,
    supplier_id  INTEGER NOT NULL REFERENCES counterparty(id),
    number       TEXT NOT NULL DEFAULT '',
    invoice_date TEXT,
    amount_kop   INTEGER,
    vat_kop      INTEGER,
    pay_by       TEXT,
    status       TEXT NOT NULL DEFAULT 'draft',
    req_inn      TEXT NOT NULL DEFAULT '',
    req_kpp      TEXT NOT NULL DEFAULT '',
    req_account  TEXT NOT NULL DEFAULT '',
    req_bank     TEXT NOT NULL DEFAULT '',
    req_bik      TEXT NOT NULL DEFAULT '',
    req_report   TEXT NOT NULL DEFAULT '',
    approved_at  TEXT,
    approved_by  TEXT NOT NULL DEFAULT '',
    reject_reason TEXT NOT NULL DEFAULT '',
    paid_at      TEXT,
    paid_amount_kop INTEGER,
    payment_number  TEXT NOT NULL DEFAULT '',
    posted_1c    INTEGER NOT NULL DEFAULT 0,
    delivery_expected TEXT,
    updated_by   TEXT NOT NULL DEFAULT '',
    updated_at   TEXT,
    status_seq          INTEGER NOT NULL DEFAULT 0,
    status_happened_at  TEXT,
    status_on_behalf_of TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    created_by   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS purchase_invoice_line (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id       INTEGER NOT NULL REFERENCES purchase_invoice(id) ON DELETE CASCADE,
    request_line_id  INTEGER REFERENCES supply_request_line(id) ON DELETE SET NULL,
    estimate_line_id INTEGER REFERENCES estimate_line(id) ON DELETE SET NULL,
    material_id      INTEGER REFERENCES material(id),
    name_text        TEXT NOT NULL DEFAULT '',
    unit             TEXT NOT NULL DEFAULT '',
    qty_x1000        INTEGER NOT NULL DEFAULT 0,
    price_kop        INTEGER,
    amount_kop       INTEGER
);

CREATE TABLE IF NOT EXISTS goods_receipt (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL REFERENCES counterparty(id),
    invoice_id  INTEGER REFERENCES purchase_invoice(id) ON DELETE SET NULL,
    project_id  INTEGER REFERENCES project(id) ON DELETE SET NULL,
    number      TEXT NOT NULL DEFAULT '',
    doc_date    TEXT,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    received_by TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft',
    posted_1c   INTEGER NOT NULL DEFAULT 0,
    updated_by  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT,
    status_seq          INTEGER NOT NULL DEFAULT 0,
    status_happened_at  TEXT,
    status_on_behalf_of TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS goods_receipt_line (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id       INTEGER NOT NULL REFERENCES goods_receipt(id) ON DELETE CASCADE,
    invoice_line_id  INTEGER REFERENCES purchase_invoice_line(id) ON DELETE SET NULL,
    estimate_line_id INTEGER REFERENCES estimate_line(id) ON DELETE SET NULL,
    material_id      INTEGER REFERENCES material(id),
    name_text        TEXT NOT NULL DEFAULT '',
    unit             TEXT NOT NULL DEFAULT '',
    qty_x1000        INTEGER NOT NULL DEFAULT 0,
    price_kop        INTEGER,
    amount_kop       INTEGER,
    claim_note       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS issue_note (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    request_id  INTEGER REFERENCES supply_request(id) ON DELETE SET NULL,
    number      TEXT NOT NULL DEFAULT '',
    direction   TEXT NOT NULL DEFAULT 'to_site',
    status      TEXT NOT NULL DEFAULT 'draft',
    picked_at    TEXT,
    picked_by    TEXT NOT NULL DEFAULT '',
    ready_at     TEXT,
    loaded_at    TEXT,
    delivered_at TEXT,
    accepted_at  TEXT,
    accepted_by  TEXT NOT NULL DEFAULT '',
    paper_returned_at TEXT,
    pass_number TEXT NOT NULL DEFAULT '',
    driver_id   INTEGER REFERENCES driver(id),
    vehicle_id  INTEGER REFERENCES vehicle(id),
    trip_id     INTEGER REFERENCES trip(id) ON DELETE SET NULL,
    posted_1c   INTEGER NOT NULL DEFAULT 0,
    updated_by  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT,
    status_seq          INTEGER NOT NULL DEFAULT 0,
    status_happened_at  TEXT,
    status_on_behalf_of TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS issue_note_line (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id          INTEGER NOT NULL REFERENCES issue_note(id) ON DELETE CASCADE,
    request_line_id  INTEGER REFERENCES supply_request_line(id) ON DELETE SET NULL,
    estimate_line_id INTEGER REFERENCES estimate_line(id) ON DELETE SET NULL,
    material_id      INTEGER REFERENCES material(id),
    name_text        TEXT NOT NULL DEFAULT '',
    unit             TEXT NOT NULL DEFAULT '',
    qty_ordered_x1000  INTEGER NOT NULL DEFAULT 0,
    qty_picked_x1000   INTEGER,
    qty_accepted_x1000 INTEGER,
    discrepancy_note   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS work_progress (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    estimate_line_id INTEGER NOT NULL REFERENCES estimate_line(id) ON DELETE CASCADE,
    qty_x1000        INTEGER NOT NULL DEFAULT 0,
    period_month     TEXT NOT NULL DEFAULT '',
    reported_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    happened_at      TEXT,
    reported_by      TEXT NOT NULL DEFAULT '',
    on_behalf_of     TEXT NOT NULL DEFAULT '',
    note             TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS work_act (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    estimate_id INTEGER REFERENCES estimate(id),
    kind        TEXT NOT NULL DEFAULT 'ks2',
    number      TEXT NOT NULL DEFAULT '',
    act_date    TEXT,
    period_from TEXT,
    period_to   TEXT,
    status      TEXT NOT NULL DEFAULT 'draft',
    amount_file_kop INTEGER,
    curator_id  INTEGER REFERENCES contact_person(id),
    sent_at     TEXT,
    visit_at    TEXT,
    signed_at   TEXT,
    rejected_at TEXT,
    reject_reason TEXT NOT NULL DEFAULT '',
    updated_by  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT,
    status_seq          INTEGER NOT NULL DEFAULT 0,
    status_happened_at  TEXT,
    status_on_behalf_of TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS work_act_line (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    act_id           INTEGER NOT NULL REFERENCES work_act(id) ON DELETE CASCADE,
    estimate_line_id INTEGER NOT NULL REFERENCES estimate_line(id),
    qty_x1000        INTEGER NOT NULL DEFAULT 0,
    price_kop        INTEGER,
    amount_kop       INTEGER,
    note             TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS closing_package (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    period_month TEXT NOT NULL DEFAULT '',
    number       TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'draft',
    curator_id   INTEGER REFERENCES contact_person(id),
    sent_at      TEXT,
    signed_at    TEXT,
    to_accounting_at TEXT,
    paid_at      TEXT,
    paid_amount_kop INTEGER,
    updated_by   TEXT NOT NULL DEFAULT '',
    updated_at   TEXT,
    status_seq          INTEGER NOT NULL DEFAULT 0,
    status_happened_at  TEXT,
    status_on_behalf_of TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    created_by   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS closing_package_act (
    package_id INTEGER NOT NULL REFERENCES closing_package(id) ON DELETE CASCADE,
    act_id     INTEGER NOT NULL REFERENCES work_act(id) ON DELETE CASCADE,
    PRIMARY KEY (package_id, act_id)
);

CREATE TABLE IF NOT EXISTS material_writeoff (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    number       TEXT NOT NULL DEFAULT '',
    period_month TEXT NOT NULL DEFAULT '',
    doc_date     TEXT,
    status       TEXT NOT NULL DEFAULT 'draft',
    approved_at  TEXT,
    approved_by  TEXT NOT NULL DEFAULT '',
    posted_1c    INTEGER NOT NULL DEFAULT 0,
    updated_by   TEXT NOT NULL DEFAULT '',
    updated_at   TEXT,
    status_seq          INTEGER NOT NULL DEFAULT 0,
    status_happened_at  TEXT,
    status_on_behalf_of TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    created_by   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS material_writeoff_line (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    writeoff_id      INTEGER NOT NULL REFERENCES material_writeoff(id) ON DELETE CASCADE,
    estimate_line_id INTEGER REFERENCES estimate_line(id) ON DELETE SET NULL,
    material_id      INTEGER REFERENCES material(id),
    name_text        TEXT NOT NULL DEFAULT '',
    unit             TEXT NOT NULL DEFAULT '',
    qty_x1000        INTEGER NOT NULL DEFAULT 0,
    qty_norm_x1000   INTEGER,
    note             TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS approval (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity     TEXT NOT NULL,
    entity_id  INTEGER NOT NULL,
    step       INTEGER NOT NULL DEFAULT 1,
    assignee_login      TEXT NOT NULL DEFAULT '',
    assignee_contact_id INTEGER REFERENCES contact_person(id),
    role_hint  TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    requested_by TEXT NOT NULL DEFAULT '',
    due_at     TEXT,
    decided_at TEXT,
    decided_by TEXT NOT NULL DEFAULT '',
    comment    TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT,
    status_seq          INTEGER NOT NULL DEFAULT 0,
    status_happened_at  TEXT,
    status_on_behalf_of TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    UNIQUE (entity, entity_id, step)
);

CREATE TABLE IF NOT EXISTS attachment (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity        TEXT NOT NULL,
    entity_id     INTEGER NOT NULL,
    project_id    INTEGER REFERENCES project(id) ON DELETE SET NULL,
    filename      TEXT NOT NULL UNIQUE,
    original_name TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'scan',
    size_bytes    INTEGER,
    sha256        TEXT NOT NULL DEFAULT '',
    owner         TEXT NOT NULL DEFAULT '',
    uploaded_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    uploaded_by   TEXT NOT NULL DEFAULT ''
);


-- ═══════════════════════════════════════════════════════════════════
-- ИНДЕКСЫ
-- ═══════════════════════════════════════════════════════════════════
CREATE UNIQUE INDEX IF NOT EXISTS idx_process_step_match
    ON process_step(match_entity, match_state) WHERE match_entity != '';
CREATE INDEX IF NOT EXISTS idx_event_entity  ON event_log(entity, entity_id, happened_at);
CREATE INDEX IF NOT EXISTS idx_event_project ON event_log(project_id, happened_at);
CREATE INDEX IF NOT EXISTS idx_event_actor   ON event_log(actor, happened_at);
CREATE INDEX IF NOT EXISTS idx_event_step    ON event_log(step_code, happened_at) WHERE step_code != '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_counterparty_inn ON counterparty(inn) WHERE inn != '';
CREATE INDEX IF NOT EXISTS idx_contact_cp ON contact_person(counterparty_id);
CREATE INDEX IF NOT EXISTS idx_project_status   ON project(status, date_end_plan);
CREATE INDEX IF NOT EXISTS idx_project_customer ON project(customer_id);
CREATE INDEX IF NOT EXISTS idx_project_foreman  ON project(foreman_login) WHERE foreman_login != '';
CREATE INDEX IF NOT EXISTS idx_material_article ON material(article) WHERE article != '';
CREATE INDEX IF NOT EXISTS idx_alias_material ON material_alias(material_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_estimate_current
    ON estimate(project_id, kind) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_eline_estimate ON estimate_line(estimate_id, ord);
CREATE INDEX IF NOT EXISTS idx_eline_material ON estimate_line(material_id) WHERE material_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_eline_parent   ON estimate_line(parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_eline_kind     ON estimate_line(estimate_id, kind);
CREATE INDEX IF NOT EXISTS idx_lmat_line ON line_material(line_id);
CREATE INDEX IF NOT EXISTS idx_quote_line     ON price_quote(estimate_line_id, quoted_at);
CREATE INDEX IF NOT EXISTS idx_quote_supplier ON price_quote(supplier_id, quoted_at);
CREATE INDEX IF NOT EXISTS idx_sreq_project ON supply_request(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sreq_status  ON supply_request(status, need_by);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sreq_number ON supply_request(project_id, number) WHERE number != '';
CREATE INDEX IF NOT EXISTS idx_sreql_request ON supply_request_line(request_id);
CREATE INDEX IF NOT EXISTS idx_sreql_eline   ON supply_request_line(estimate_line_id);
CREATE INDEX IF NOT EXISTS idx_pinv_status   ON purchase_invoice(status, pay_by);
CREATE INDEX IF NOT EXISTS idx_pinv_project  ON purchase_invoice(project_id, invoice_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pinv_number
    ON purchase_invoice(supplier_id, number) WHERE number != '';
CREATE INDEX IF NOT EXISTS idx_pinvl_invoice ON purchase_invoice_line(invoice_id);
CREATE INDEX IF NOT EXISTS idx_pinvl_reqline ON purchase_invoice_line(request_line_id);
CREATE INDEX IF NOT EXISTS idx_pinvl_eline   ON purchase_invoice_line(estimate_line_id);
CREATE INDEX IF NOT EXISTS idx_grec_supplier ON goods_receipt(supplier_id, doc_date);
CREATE INDEX IF NOT EXISTS idx_grec_status   ON goods_receipt(status, received_at);
CREATE INDEX IF NOT EXISTS idx_grecl_receipt  ON goods_receipt_line(receipt_id);
CREATE INDEX IF NOT EXISTS idx_grecl_material ON goods_receipt_line(material_id);
CREATE INDEX IF NOT EXISTS idx_inote_project ON issue_note(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_inote_status  ON issue_note(status, created_at);
CREATE INDEX IF NOT EXISTS idx_inote_trip    ON issue_note(trip_id) WHERE trip_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_inote_number
    ON issue_note(project_id, number) WHERE number != '';
CREATE INDEX IF NOT EXISTS idx_inotel_note     ON issue_note_line(note_id);
CREATE INDEX IF NOT EXISTS idx_inotel_material ON issue_note_line(material_id);
CREATE INDEX IF NOT EXISTS idx_inotel_eline    ON issue_note_line(estimate_line_id);
CREATE INDEX IF NOT EXISTS idx_wprog_line    ON work_progress(estimate_line_id, reported_at);
CREATE INDEX IF NOT EXISTS idx_wprog_project ON work_progress(project_id, period_month);
CREATE INDEX IF NOT EXISTS idx_wact_project ON work_act(project_id, period_to);
CREATE INDEX IF NOT EXISTS idx_wact_status  ON work_act(status, act_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wact_number
    ON work_act(project_id, kind, number) WHERE number != '';
CREATE INDEX IF NOT EXISTS idx_wactl_act   ON work_act_line(act_id);
CREATE INDEX IF NOT EXISTS idx_wactl_eline ON work_act_line(estimate_line_id);
CREATE INDEX IF NOT EXISTS idx_cpack_project ON closing_package(project_id, period_month);
CREATE INDEX IF NOT EXISTS idx_woff_project ON material_writeoff(project_id, period_month);
CREATE INDEX IF NOT EXISTS idx_woffl_woff ON material_writeoff_line(writeoff_id);
CREATE INDEX IF NOT EXISTS idx_approval_entity   ON approval(entity, entity_id);
CREATE INDEX IF NOT EXISTS idx_approval_assignee ON approval(assignee_login, status, requested_at);
CREATE INDEX IF NOT EXISTS idx_approval_pending  ON approval(status, due_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_attach_entity ON attachment(entity, entity_id);


-- ═══════════════════════════════════════════════════════════════════
-- ПРЕДСТАВЛЕНИЯ
-- ═══════════════════════════════════════════════════════════════════
DROP VIEW IF EXISTS v_estimate_supply;
CREATE VIEW v_estimate_supply AS
SELECT e.* FROM estimate e
 WHERE e.is_current = 1
   AND e.kind = COALESCE((SELECT 'internal' FROM estimate i
                           WHERE i.project_id = e.project_id
                             AND i.kind = 'internal' AND i.is_current = 1 LIMIT 1),
                         'customer');

DROP VIEW IF EXISTS v_estimate_closing;
CREATE VIEW v_estimate_closing AS
SELECT e.* FROM estimate e
 WHERE e.is_current = 1
   AND e.kind = COALESCE((SELECT 'customer' FROM estimate c
                           WHERE c.project_id = e.project_id
                             AND c.kind = 'customer' AND c.is_current = 1 LIMIT 1),
                         'internal');

DROP VIEW IF EXISTS v_material_flow;
CREATE VIEW v_material_flow AS
SELECT e.project_id, l.material_id, l.id AS estimate_line_id,
       'budget' AS stage, NULL AS place,
       l.qty_x1000 AS qty_x1000, COALESCE(l.amount_kop, 0) AS amount_kop,
       e.created_at AS at, 'estimate' AS doc_type, e.id AS doc_id
  FROM estimate_line l
  JOIN v_estimate_supply e ON e.id = l.estimate_id
 WHERE l.is_deleted = 0 AND l.kind IN ('material', 'equipment') AND l.material_id IS NOT NULL

UNION ALL
SELECT r.project_id, l.material_id, l.estimate_line_id,
       'requested', NULL, l.qty_x1000, 0, r.created_at, 'supply_request', r.id
  FROM supply_request_line l
  JOIN supply_request r ON r.id = l.request_id
 WHERE l.cancelled_at IS NULL
   AND r.status NOT IN ('cancelled', 'rejected', 'draft')
   AND l.material_id IS NOT NULL

UNION ALL
SELECT COALESCE(i.project_id, (SELECT e2.project_id FROM estimate_line el2
                                 JOIN estimate e2 ON e2.id = el2.estimate_id
                                WHERE el2.id = l.estimate_line_id)),
       l.material_id, l.estimate_line_id,
       'ordered', NULL, l.qty_x1000, COALESCE(l.amount_kop, 0),
       i.created_at, 'purchase_invoice', i.id
  FROM purchase_invoice_line l
  JOIN purchase_invoice i ON i.id = l.invoice_id
 WHERE i.status NOT IN ('cancelled', 'rejected', 'draft') AND l.material_id IS NOT NULL

UNION ALL
SELECT COALESCE(i.project_id, (SELECT e2.project_id FROM estimate_line el2
                                 JOIN estimate e2 ON e2.id = el2.estimate_id
                                WHERE el2.id = l.estimate_line_id)),
       l.material_id, l.estimate_line_id,
       'paid', NULL, l.qty_x1000, COALESCE(l.amount_kop, 0),
       i.paid_at, 'purchase_invoice', i.id
  FROM purchase_invoice_line l
  JOIN purchase_invoice i ON i.id = l.invoice_id
 WHERE i.status = 'paid' AND l.material_id IS NOT NULL

UNION ALL
SELECT g.project_id, l.material_id, l.estimate_line_id,
       'stock_in', 'warehouse', l.qty_x1000, COALESCE(l.amount_kop, 0),
       g.received_at, 'goods_receipt', g.id
  FROM goods_receipt_line l
  JOIN goods_receipt g ON g.id = l.receipt_id
 WHERE g.status = 'posted' AND l.material_id IS NOT NULL

UNION ALL
SELECT n.project_id, l.material_id, l.estimate_line_id,
       CASE WHEN n.direction = 'to_site' THEN 'stock_out' ELSE 'stock_back' END,
       'warehouse',
       CASE WHEN n.direction = 'to_site'
            THEN -COALESCE(l.qty_picked_x1000, l.qty_ordered_x1000)
            ELSE  COALESCE(l.qty_accepted_x1000, l.qty_picked_x1000, l.qty_ordered_x1000) END,
       0, COALESCE(n.loaded_at, n.picked_at, n.created_at), 'issue_note', n.id
  FROM issue_note_line l
  JOIN issue_note n ON n.id = l.note_id
 WHERE n.status IN ('in_transit', 'delivered', 'accepted', 'discrepancy', 'closed')
   AND l.material_id IS NOT NULL

UNION ALL
SELECT n.project_id, l.material_id, l.estimate_line_id,
       CASE WHEN n.direction = 'to_site' THEN 'site_in' ELSE 'site_back' END,
       'site',
       CASE WHEN n.direction = 'to_site'
            THEN  COALESCE(l.qty_accepted_x1000, 0)
            ELSE -COALESCE(l.qty_accepted_x1000, l.qty_picked_x1000, l.qty_ordered_x1000) END,
       0, COALESCE(n.accepted_at, n.delivered_at), 'issue_note', n.id
  FROM issue_note_line l
  JOIN issue_note n ON n.id = l.note_id
 WHERE n.status IN ('accepted', 'discrepancy', 'closed') AND l.material_id IS NOT NULL

UNION ALL
SELECT w.project_id, l.material_id, l.estimate_line_id,
       'site_off', 'site', -l.qty_x1000, 0,
       w.doc_date, 'material_writeoff', w.id
  FROM material_writeoff_line l
  JOIN material_writeoff w ON w.id = l.writeoff_id
 WHERE w.status IN ('approved', 'posted') AND l.material_id IS NOT NULL;


DROP VIEW IF EXISTS v_material_balance;
CREATE VIEW v_material_balance AS
SELECT f.project_id, f.material_id,
       SUM(CASE WHEN f.stage = 'budget'    THEN f.qty_x1000 ELSE 0 END) AS qty_budget_x1000,
       SUM(CASE WHEN f.stage = 'requested' THEN f.qty_x1000 ELSE 0 END) AS qty_requested_x1000,
       SUM(CASE WHEN f.stage = 'ordered'   THEN f.qty_x1000 ELSE 0 END) AS qty_ordered_x1000,
       SUM(CASE WHEN f.stage = 'paid'      THEN f.qty_x1000 ELSE 0 END) AS qty_paid_x1000,
       SUM(CASE WHEN f.place = 'warehouse' THEN f.qty_x1000 ELSE 0 END) AS qty_in_stock_x1000,
       SUM(CASE WHEN f.stage = 'site_in'   THEN f.qty_x1000 ELSE 0 END) AS qty_accepted_x1000,
       SUM(CASE WHEN f.place = 'site'      THEN f.qty_x1000 ELSE 0 END) AS qty_on_site_x1000,
       SUM(CASE WHEN f.stage = 'site_off'  THEN -f.qty_x1000 ELSE 0 END) AS qty_written_off_x1000,
       SUM(CASE WHEN f.stage = 'stock_out' THEN -f.qty_x1000
                WHEN f.stage = 'site_in'   THEN -f.qty_x1000 ELSE 0 END) AS qty_unconfirmed_x1000,
       MAX(CASE WHEN f.stage = 'stock_out' THEN f.at END) AS last_shipment_at,
       COUNT(*) AS doc_rows,
       SUM(CASE WHEN f.stage = 'ordered' THEN f.amount_kop ELSE 0 END) AS amount_ordered_kop,
       SUM(CASE WHEN f.stage = 'paid'    THEN f.amount_kop ELSE 0 END) AS amount_paid_kop,
       SUM(CASE WHEN f.stage = 'budget'  THEN f.amount_kop ELSE 0 END) AS amount_budget_kop
  FROM v_material_flow f
 GROUP BY f.project_id, f.material_id;


DROP VIEW IF EXISTS v_work_balance;
CREATE VIEW v_work_balance AS
SELECT e.project_id, l.id AS estimate_line_id,
       l.pos_no, l.code, l.name, l.unit,
       l.qty_x1000 AS qty_budget_x1000,
       COALESCE(l.amount_kop, 0) AS amount_budget_kop,
       COALESCE((SELECT SUM(p.qty_x1000) FROM work_progress p
                  WHERE p.estimate_line_id = l.id), 0) AS qty_reported_x1000,
       COALESCE((SELECT SUM(al.qty_x1000) FROM work_act_line al
                  JOIN work_act a ON a.id = al.act_id
                 WHERE al.estimate_line_id = l.id
                   AND a.status IN ('signed', 'closed')), 0) AS qty_signed_x1000,
       COALESCE((SELECT SUM(al.qty_x1000) FROM work_act_line al
                  JOIN work_act a ON a.id = al.act_id
                 WHERE al.estimate_line_id = l.id
                   AND a.status NOT IN ('signed', 'closed', 'cancelled')), 0) AS qty_pending_x1000
  FROM estimate_line l
  JOIN v_estimate_closing e ON e.id = l.estimate_id
 WHERE l.is_deleted = 0 AND l.kind = 'work';


DROP VIEW IF EXISTS v_writeoff_norm;
CREATE VIEW v_writeoff_norm AS
SELECT b.project_id, lm.material_id,
       SUM(b.qty_signed_x1000 * lm.qty_per_unit_x1000
           * (10000 + lm.waste_pct_x100) / 10000 / 1000) AS qty_norm_x1000
  FROM v_work_balance b
  JOIN line_material lm ON lm.line_id = b.estimate_line_id
 GROUP BY b.project_id, lm.material_id;


DROP VIEW IF EXISTS v_material_unmatched;
CREATE VIEW v_material_unmatched AS
SELECT 'estimate_line' AS doc_type, l.id AS line_id, e.project_id,
       l.name AS name_text, l.qty_x1000
  FROM estimate_line l JOIN v_estimate_supply e ON e.id = l.estimate_id
 WHERE l.material_id IS NULL AND l.kind IN ('material','equipment') AND l.is_deleted = 0
UNION ALL
SELECT 'purchase_invoice_line', l.id, i.project_id, l.name_text, l.qty_x1000
  FROM purchase_invoice_line l JOIN purchase_invoice i ON i.id = l.invoice_id
 WHERE l.material_id IS NULL
UNION ALL
SELECT 'goods_receipt_line', l.id, g.project_id, l.name_text, l.qty_x1000
  FROM goods_receipt_line l JOIN goods_receipt g ON g.id = l.receipt_id
 WHERE l.material_id IS NULL
UNION ALL
SELECT 'issue_note_line', l.id, n.project_id, l.name_text, l.qty_ordered_x1000
  FROM issue_note_line l JOIN issue_note n ON n.id = l.note_id
 WHERE l.material_id IS NULL;


DROP VIEW IF EXISTS v_state_duration;
CREATE VIEW v_state_duration AS
SELECT e.entity, e.entity_id, e.project_id,
       e.to_state AS state, e.step_code,
       e.actor AS entered_by, e.is_backdated,
       e.happened_at AS entered_at,
       (SELECT MIN(n.happened_at) FROM event_log n
         WHERE n.entity = e.entity AND n.entity_id = e.entity_id
           AND n.action = 'status'
           AND (n.happened_at > e.happened_at
                OR (n.happened_at = e.happened_at AND n.id > e.id))) AS left_at,
       CAST(ROUND((julianday(
              (SELECT MIN(n.happened_at) FROM event_log n
                WHERE n.entity = e.entity AND n.entity_id = e.entity_id
                  AND n.action = 'status'
                  AND (n.happened_at > e.happened_at
                       OR (n.happened_at = e.happened_at AND n.id > e.id)))
            ) - julianday(e.happened_at)) * 1440) AS INTEGER) AS minutes_in_state
  FROM event_log e
 WHERE e.action = 'status';


DROP VIEW IF EXISTS v_process_step_time;
CREATE VIEW v_process_step_time AS
SELECT s.code, s.step_no, s.flow, s.lane, s.title, s.media_before,
       COUNT(CASE WHEN d.is_backdated = 0 THEN d.minutes_in_state END) AS samples,
       COUNT(CASE WHEN d.is_backdated = 1 THEN d.minutes_in_state END) AS samples_backdated,
       AVG(CASE WHEN d.is_backdated = 0 THEN d.minutes_in_state END)   AS avg_minutes,
       MIN(CASE WHEN d.is_backdated = 0 THEN d.minutes_in_state END)   AS min_minutes,
       MAX(CASE WHEN d.is_backdated = 0 THEN d.minutes_in_state END)   AS max_minutes
  FROM process_step s
  LEFT JOIN v_state_duration d ON d.step_code = s.code
 GROUP BY s.code, s.step_no, s.flow, s.lane, s.title, s.media_before;


DROP VIEW IF EXISTS v_open_documents;
CREATE VIEW v_open_documents AS
SELECT 'purchase_invoice' AS entity, i.id AS entity_id, i.project_id,
       i.number, i.status, COALESCE(ws.title, i.status) AS status_title,
       COALESCE(i.updated_at, i.created_at) AS since, ws.sla_hours,
       (SELECT a.assignee_login FROM approval a
         WHERE a.entity='purchase_invoice' AND a.entity_id=i.id AND a.status='pending'
         ORDER BY a.step LIMIT 1) AS waiting_for
  FROM purchase_invoice i
  LEFT JOIN workflow_state ws ON ws.entity='purchase_invoice' AND ws.code=i.status
 WHERE COALESCE(ws.is_final, 0) = 0
UNION ALL
SELECT 'issue_note', n.id, n.project_id, n.number, n.status,
       COALESCE(ws.title, n.status), COALESCE(n.updated_at, n.created_at), ws.sla_hours, ''
  FROM issue_note n
  LEFT JOIN workflow_state ws ON ws.entity='issue_note' AND ws.code=n.status
 WHERE COALESCE(ws.is_final, 0) = 0
UNION ALL
SELECT 'work_act', a.id, a.project_id, a.number, a.status,
       COALESCE(ws.title, a.status), COALESCE(a.updated_at, a.created_at), ws.sla_hours, ''
  FROM work_act a
  LEFT JOIN workflow_state ws ON ws.entity='work_act' AND ws.code=a.status
 WHERE COALESCE(ws.is_final, 0) = 0
UNION ALL
SELECT 'supply_request', r.id, r.project_id, r.number, r.status,
       COALESCE(ws.title, r.status), COALESCE(r.updated_at, r.created_at), ws.sla_hours,
       r.assignee_login
  FROM supply_request r
  LEFT JOIN workflow_state ws ON ws.entity='supply_request' AND ws.code=r.status
 WHERE COALESCE(ws.is_final, 0) = 0;

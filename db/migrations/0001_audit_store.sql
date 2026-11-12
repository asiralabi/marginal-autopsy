-- Audit store: every audit run as a snapshot, plus the verdict ledger.
-- Runs on the analyst's machine (or a self-hosted edition), never in the cloud:
-- these tables hold pseudonymous project records (playbook section 23).
-- Money is never calculated here; src/ does the arithmetic and this stores the result.
-- Text columns stay text and blank values are NULL, as in the CSV schema.

create schema if not exists audit;

create table audit.agencies (
  agency_id text primary key,
  region text,
  industry text,
  employee_band text,
  project_type text,
  currency text,
  billing_model text,
  costing_method text,
  data_permission_status text,
  first_loaded_at timestamptz not null default now()
);

-- One row per `store load`: a complete snapshot of the agency folder's results.
create table audit.runs (
  run_id bigint generated always as identity primary key,
  agency_id text not null references audit.agencies on delete cascade,
  loaded_at timestamptz not null default now(),
  generated_on date,                 -- report date the engine was given
  source_folder text not null,       -- the agency folder on the analyst's machine
  mapping jsonb,                     -- mapping.json in force for this run, if any
  rule_versions jsonb not null,      -- {"F1": 1, ...} from src/rules.toml
  engine_commit text                 -- git commit of the engine, when known
);
create index runs_agency_id_idx on audit.runs (agency_id, run_id desc);

create table audit.projects (
  run_id bigint not null references audit.runs on delete cascade,
  project_id text not null,
  agency_id text,
  start_date date,
  end_date date,
  project_type text,
  contract_value numeric,
  estimated_labor_hours numeric,
  estimated_labor_cost numeric,
  estimated_other_cost numeric,
  estimated_subcontractor_cost numeric,
  planned_margin numeric,
  actual_revenue numeric,
  actual_cost numeric,
  currency text,
  project_status text,
  exclusion_reason text,
  primary key (run_id, project_id)
);

create table audit.scope_items (
  run_id bigint not null references audit.runs on delete cascade,
  scope_item_id text not null,
  project_id text,
  description text,
  source_document_id text,
  included_in_original_scope boolean,
  change_request_date date,
  change_approval_status text,
  agreed_price_change numeric,
  billable_status text,
  review_notes text,
  primary key (run_id, scope_item_id)
);
create index scope_items_project_idx on audit.scope_items (run_id, project_id);

create table audit.time_entries (
  run_id bigint not null references audit.runs on delete cascade,
  entry_id text not null,
  project_id text,
  date date,
  role_or_pseudonym text,
  task_id text,
  hours numeric,
  hourly_cost_rate numeric,
  cost_amount numeric,
  source_system text,
  entry_confidence text,
  primary key (run_id, entry_id)
);
create index time_entries_project_idx on audit.time_entries (run_id, project_id);

create table audit.financial_transactions (
  run_id bigint not null references audit.runs on delete cascade,
  transaction_id text not null,
  project_id text,
  transaction_date date,
  transaction_type text,
  amount numeric,
  currency text,
  fx_rate_to_usd numeric,
  fx_rate_source text,
  vendor_or_customer_ref text,
  invoice_id text,
  source_document_id text,
  allocation_method text,
  is_estimated boolean,
  primary key (run_id, transaction_id)
);
create index financial_transactions_project_idx on audit.financial_transactions (run_id, project_id);

create table audit.evidence_documents (
  run_id bigint not null references audit.runs on delete cascade,
  document_id text not null,
  project_id text,
  document_type text,
  file_reference text,
  source_date date,
  relevant_page_or_row text,
  extracted_fact text,
  verification_status text,
  primary key (run_id, document_id)
);
create index evidence_documents_project_idx on audit.evidence_documents (run_id, project_id);

-- reconcile.py output, one row per project
create table audit.baseline (
  run_id bigint not null references audit.runs on delete cascade,
  project_id text not null,
  agency_id text,
  currency text,
  included boolean,
  contract_value numeric,
  planned_cost numeric,
  planned_profit numeric,
  planned_margin_pct numeric,
  invoiced_revenue numeric,
  cash_received numeric,
  labour_hours numeric,
  labour_hours_without_cost numeric,
  labour_cost numeric,
  subcontractor_cost numeric,
  other_cost numeric,
  actual_cost numeric,
  actual_profit numeric,
  actual_margin_pct numeric,
  profit_variance numeric,
  data_complete boolean,
  fx_used text,
  notes text,
  primary key (run_id, project_id)
);

-- detect_variances.py output. Verdict columns are not stored here: the ledger
-- below is the only record of verdicts.
create table audit.findings (
  run_id bigint not null references audit.runs on delete cascade,
  finding_id text not null,
  project_id text,
  finding_type text,
  rule_version integer,
  description text,
  evidence_ids text[] not null default '{}',
  baseline_amount numeric,
  observed_amount numeric,
  estimated_impact numeric,
  calculation text,
  currency text,
  impact_type text,
  confidence text,
  alternative_explanation text,
  next_action text,
  primary key (run_id, finding_id)
);
create index findings_project_idx on audit.findings (run_id, project_id);

create table audit.normalization_log (
  run_id bigint not null references audit.runs on delete cascade,
  seq integer not null,
  level text,
  table_name text,
  line integer,
  record_id text,
  column_name text,
  original text,
  normalized text,
  note text,
  primary key (run_id, seq)
);

-- The verdict ledger: append-only. A new row is written whenever a finding's review
-- changes, so the history of who decided what, and when, is never lost.
create table audit.verdict_events (
  event_id bigint generated always as identity primary key,
  agency_id text not null references audit.agencies on delete cascade,
  finding_id text not null,
  analyst_decision text not null default '' check (analyst_decision in ('', 'keep', 'drop')),
  analyst_note text not null default '',
  customer_verdict text not null default 'unreviewed'
    check (customer_verdict in ('unreviewed', 'confirmed', 'partially_confirmed', 'rejected', 'needs_more_evidence')),
  already_known text not null default '' check (already_known in ('', 'yes', 'no')),
  actionability text not null default '' check (actionability in ('', 'yes', 'no')),
  agreed_amount numeric,
  agency_comment text not null default '',
  reviewer_id text not null default '',
  reviewed_on date,
  recorded_at timestamptz not null default now(),
  source text not null default 'verdicts.csv'
);
create index verdict_events_finding_idx on audit.verdict_events (agency_id, finding_id, event_id desc);

-- Latest ledger entry per finding.
create view audit.verdicts_current as
  select distinct on (agency_id, finding_id) *
  from audit.verdict_events
  order by agency_id, finding_id, event_id desc;

-- The most recent run per agency.
create view audit.latest_runs as
  select distinct on (agency_id) *
  from audit.runs
  order by agency_id, run_id desc;

-- Findings of each agency's latest run with their current verdict. Joins only;
-- confirmed totals are calculated by src/review.py, not here.
create view audit.findings_current as
  select r.agency_id, f.*,
         coalesce(v.analyst_decision, '') as analyst_decision,
         coalesce(v.customer_verdict, 'unreviewed') as customer_verdict,
         v.agreed_amount, v.reviewer_id, v.reviewed_on
  from audit.findings f
  join audit.latest_runs r on r.run_id = f.run_id
  left join audit.verdicts_current v on v.agency_id = r.agency_id and v.finding_id = f.finding_id;

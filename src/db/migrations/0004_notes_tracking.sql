alter table offres add column if not exists notes text;
alter table offres add column if not exists gmail_message_id text;

create table if not exists pipeline_runs (
  id bigserial primary key,
  action text not null,
  count_result int,
  error text,
  created_at timestamp default now()
);

-- v5 P5 — backfill ownership for pack ticket boards that were generated BEFORE the
-- owner was threaded through the pipeline (api/app.py::start_audit ->
-- jobs.spawn_audit -> Orchestrator.audit_cycle -> generate_tickets_from_run).
--
-- Why these rows exist: `_generate_tickets` deliberately passed no owner, on the theory
-- that a logged-in viewer would stamp it lazily. But that lazy stamp only ran when the
-- board did not exist yet — and generation is what makes it exist — so every board the
-- pipeline produced was born unowned and stayed that way. `_require_ticket_owner` reads
-- `pack_owner_of() IS NULL` as "anonymous, leave open", so the P5 gate was inert for all
-- of them: any logged-in user holding any entitlement on the domain could close another
-- user's tickets, which drives progressive unlock and spends crawl budget.
--
-- The rule here is deliberately conservative. A row is claimed only when the evidence is
-- UNAMBIGUOUS: exactly one distinct user holds a live, access-granting entitlement on that
-- client's domain. Anything else — nobody entitled (a genuinely anonymous free board, which
-- must keep working signed-out), or two or more entitled users (an agency seat, a shared
-- domain, a refund-and-repurchase) — is left unowned rather than guessed at. Guessing wrong
-- would lock the real owner out of their own board, which is worse than the status quo.
--
-- 'free_overview' is excluded on purpose: it is the anonymous tier's marker, not evidence
-- that someone owns the deep-value board.
--
-- Idempotent: only ever writes rows where owner_user_id IS NULL, so re-running is a no-op.

UPDATE implementation_milestones m
   SET owner_user_id = sole.user_id
  FROM (
        -- MIN(uuid) only exists from PostgreSQL 17; cast through text so this applies on
        -- 15/16 too. HAVING guarantees one distinct value, so which one we pick is moot.
        SELECT c.id AS client_id, MIN(e.user_id::text)::uuid AS user_id
          FROM clients c
          JOIN entitlements e ON e.domain = c.domain
         WHERE (e.expires_at IS NULL OR e.expires_at > NOW())
           AND e.scope IN ('pack', 'all_packs', 'tickets')
         GROUP BY c.id
        HAVING COUNT(DISTINCT e.user_id) = 1
       ) AS sole
 WHERE m.client_id = sole.client_id
   AND m.milestone_key LIKE 'pack:%'
   AND m.owner_user_id IS NULL;

-- Counts for the operator, so "did the backfill do anything?" is answerable rather than
-- assumed. psycopg2 surfaces these on conn.notices; Postgres also honours them at
-- client_min_messages=notice. The authoritative check after deploying is the query in
-- DEPLOY.md ("Verifying the P5 ownership backfill"), which reads the same numbers back out
-- of the table and does not depend on notices being captured anywhere.
DO $$
DECLARE
    owned     INTEGER;
    ambiguous INTEGER;
BEGIN
    SELECT COUNT(DISTINCT client_id) INTO owned
      FROM implementation_milestones
     WHERE milestone_key LIKE 'pack:%' AND owner_user_id IS NOT NULL;
    SELECT COUNT(DISTINCT client_id) INTO ambiguous
      FROM implementation_milestones
     WHERE milestone_key LIKE 'pack:%' AND owner_user_id IS NULL;
    RAISE NOTICE 'pack ownership backfill: % client(s) owned, % left unowned '
                 '(anonymous or ambiguous — these stay open until an authenticated read '
                 'claims them)', owned, ambiguous;
END $$;

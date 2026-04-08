-- ═══════════════════════════════════════════════════════════
-- FIX: Set passwords for Sandhya B and Hariharann J
-- These ambassadors have NULL password_hash so they can't login
--
-- PASSWORDS BEING SET:
--   Sandhya B    (8610054611) → password: Sandhya@123
--   Hariharann J (8903784163) → password: Hariharan@123
--
-- The hashes below are SHA-256 of those passwords
-- (same as Python: hashlib.sha256(password.encode()).hexdigest())
-- ═══════════════════════════════════════════════════════════

-- Enable pgcrypto if not already enabled
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Sandhya B (phone: 8610054611) ─────────────────────────
UPDATE ambassadors
SET
    password_hash = encode(digest('Sandhya@123', 'sha256'), 'hex'),
    status = 'active'
WHERE phone = '8610054611';

-- ── Hariharann J (phone: 8903784163) ──────────────────────
UPDATE ambassadors
SET
    password_hash = encode(digest('Hariharan@123', 'sha256'), 'hex'),
    status = 'active'
WHERE phone = '8903784163';

-- ── VERIFY: Check all 3 ambassadors have hashes now ───────
SELECT name, phone, status,
       length(password_hash) AS hash_len,
       LEFT(password_hash, 12) || '...' AS hash_preview
FROM ambassadors
ORDER BY name;

-- ═══════════════════════════════════════════════════════════
-- AFTER RUNNING THIS:
--   Sandhya B    → login: 8610054611 / Sandhya@123
--   Hariharann J → login: 8903784163 / Hariharan@123
--   (Admin can change passwords anytime via Admin Dashboard)
-- ═══════════════════════════════════════════════════════════

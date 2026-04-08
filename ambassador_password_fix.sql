-- ════════════════════════════════════════════════════════
-- NEARVA AMBASSADOR PASSWORD RESET
-- Run this in Supabase SQL Editor to fix login
-- ════════════════════════════════════════════════════════

-- This sets the password for ALL ambassadors directly.
-- SHA-256 hash stored as 64-char hex string.
-- Your Python server uses hashlib.sha256(password).hexdigest()

-- ── Step 1: Check current state ──────────────────────────
SELECT id, name, phone, 
       CASE WHEN password_hash IS NULL THEN 'NO PASSWORD' 
            WHEN length(password_hash) = 64 THEN 'SHA-256 ✓'
            ELSE 'OTHER HASH' END AS hash_type
FROM ambassadors;

-- ── Step 2: Set passwords directly ──────────────────────
-- SHA-256("Tharunika1505") = the hash below
-- You can verify at: https://emn178.github.io/online-tools/sha256.html

-- For Tharunika N (phone: 9655981791) password: Tharunika1505
UPDATE ambassadors 
SET password_hash = encode(digest('Tharunika1505', 'sha256'), 'hex'),
    status = 'active'
WHERE phone = '9655981791';

-- For Hariharann J (phone: 8903784163) - set a password
UPDATE ambassadors 
SET password_hash = encode(digest('Nearva@2024', 'sha256'), 'hex'),
    status = 'active'
WHERE phone = '8903784163';

-- For Sandhya B (phone: 8610054611) - set a password
UPDATE ambassadors 
SET password_hash = encode(digest('Nearva@2024', 'sha256'), 'hex'),
    status = 'active'
WHERE phone = '8610054611';

-- ── Step 3: Verify passwords were saved ─────────────────
SELECT id, name, phone, status, 
       LEFT(password_hash, 12) || '...' AS hash_preview,
       length(password_hash) AS hash_length
FROM ambassadors;

-- ════════════════════════════════════════════════════════
-- AFTER RUNNING THIS SQL:
-- Tharunika → Login: 9655981791 / Tharunika1505
-- Others    → Login: <phone>    / Nearva@2024
-- ════════════════════════════════════════════════════════

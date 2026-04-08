-- Nearva Ambassador System Migration
-- Run this in your Supabase SQL Editor

-- 1. Create ambassadors table if it doesn't exist
CREATE TABLE IF NOT EXISTS ambassadors (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    area TEXT NOT NULL,
    password_hash TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 2. Add password_hash column if table already exists (safe idempotent alter)
ALTER TABLE ambassadors ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE ambassadors ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';
ALTER TABLE ambassadors ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

-- 3. Enable Row Level Security (public access for now – tighten later)
ALTER TABLE ambassadors ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Public read ambassadors" ON ambassadors;
CREATE POLICY "Public read ambassadors" ON ambassadors FOR ALL USING (true);

-- 4. Index for fast phone lookup (login)
CREATE INDEX IF NOT EXISTS idx_ambassadors_phone ON ambassadors(phone);

-- Done!
SELECT 'Ambassador migration complete.' AS result;

-- Nearva Robust Migration: Ambassadors & Worker Linkage
-- Run this in your Supabase SQL Editor (https://supabase.com/dashboard/project/_/sql)
-- This script safely adds missing columns even if the tables already exist.

DO $$ 
BEGIN 
    -- 1. Create Ambassadors Table if missing
    CREATE TABLE IF NOT EXISTS public.ambassadors (
        id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT NOT NULL UNIQUE,
        area TEXT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
    );

    -- 2. Ensure all columns exist in Ambassadors (Fixes "referral_code does not exist" error)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='ambassadors' AND column_name='referral_code') THEN
        ALTER TABLE public.ambassadors ADD COLUMN referral_code TEXT UNIQUE;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='ambassadors' AND column_name='password_hash') THEN
        ALTER TABLE public.ambassadors ADD COLUMN password_hash TEXT;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='ambassadors' AND column_name='status') THEN
        ALTER TABLE public.ambassadors ADD COLUMN status TEXT DEFAULT 'active' CHECK (status IN ('active', 'inactive'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='ambassadors' AND column_name='updated_at') THEN
        ALTER TABLE public.ambassadors ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now());
    END IF;

    -- 3. Link Workers to Ambassadors
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='workers' AND column_name='ambassador_id') THEN
        ALTER TABLE public.workers ADD COLUMN ambassador_id UUID REFERENCES public.ambassadors(id) ON DELETE SET NULL;
    END IF;
END $$;

-- 4. Enable RLS (Run outside block for safety)
ALTER TABLE public.ambassadors ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow Public Read Access on ambassadors" ON public.ambassadors;
CREATE POLICY "Allow Public Read Access on ambassadors" ON public.ambassadors FOR SELECT USING (true);

DROP POLICY IF EXISTS "Allow Public Insert Access on ambassadors" ON public.ambassadors;
CREATE POLICY "Allow Public Insert Access on ambassadors" ON public.ambassadors FOR INSERT WITH CHECK (true);

DROP POLICY IF EXISTS "Allow Admin Update/Delete on ambassadors" ON public.ambassadors;
CREATE POLICY "Allow Admin Update/Delete on ambassadors" ON public.ambassadors FOR ALL USING (true);

-- 5. Seed Initial referral_codes for existing ambassadors
UPDATE public.ambassadors 
SET referral_code = 'NEARVA-' || upper(substring(id::text from 1 for 4))
WHERE referral_code IS NULL;

-- 6. Initial Indexing
CREATE INDEX IF NOT EXISTS idx_workers_ambassador_id ON public.workers(ambassador_id);
CREATE INDEX IF NOT EXISTS idx_ambassadors_referral ON public.ambassadors(referral_code);

-- Done!
SELECT 'Ambassador-Worker Relationship established successfully.' AS status;

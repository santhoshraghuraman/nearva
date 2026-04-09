-- Migration: Worker Verification System

-- 1. Add verification_status to workers
ALTER TABLE public.workers 
ADD COLUMN IF NOT EXISTS verification_status TEXT DEFAULT 'PENDING' 
CHECK (verification_status IN ('PENDING', 'UNDER_REVIEW', 'VERIFIED', 'REJECTED'));

-- Update existing workers to VERIFIED so we don't break existing active workers
UPDATE public.workers SET verification_status = 'VERIFIED' WHERE account_status = 'Approved';
UPDATE public.workers SET verification_status = 'PENDING' WHERE verification_status IS NULL;

-- 2. Create Verifications Table
CREATE TABLE IF NOT EXISTS public.verifications (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    worker_id UUID REFERENCES public.workers(id) ON DELETE CASCADE,
    id_front_url TEXT NOT NULL,
    id_back_url TEXT NOT NULL,
    selfie_url TEXT NOT NULL,
    status TEXT DEFAULT 'UNDER_REVIEW' CHECK (status IN ('UNDER_REVIEW', 'VERIFIED', 'REJECTED')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- 3. Enable RLS
ALTER TABLE public.verifications ENABLE ROW LEVEL SECURITY;

-- 4. Policies for Verifications
CREATE POLICY "Allow Public Insert" ON public.verifications
FOR INSERT WITH CHECK (true);

CREATE POLICY "Allow All Access" ON public.verifications
FOR ALL USING (true);

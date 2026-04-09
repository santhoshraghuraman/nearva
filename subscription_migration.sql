-- Migration: Worker Subscription System
-- Goals: Track free period (90 days) and monthly subscriptions (₹299/mo)

-- 1. Add subscription columns to workers table
ALTER TABLE public.workers 
ADD COLUMN IF NOT EXISTS subscription_status TEXT DEFAULT 'FREE'
CHECK (subscription_status IN ('FREE', 'ACTIVE', 'EXPIRED'));

ALTER TABLE public.workers 
ADD COLUMN IF NOT EXISTS subscription_expiry TIMESTAMP WITH TIME ZONE DEFAULT NULL;

-- 2. Indexing for performance (Optional but recommended for large datasets)
CREATE INDEX IF NOT EXISTS idx_workers_subscription ON public.workers(subscription_status, subscription_expiry);

-- 3. Update existing workers: 
-- We don't need to force update them now, the backend will handle 'FREE' users based on created_at.

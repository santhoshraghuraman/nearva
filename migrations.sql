-- Feedbacks Table for Star Ratings and Comments
CREATE TABLE IF NOT EXISTS public.feedbacks (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    job_id UUID REFERENCES public.job_requests(id) NOT NULL,
    user_id UUID REFERENCES public.users(id) NOT NULL,
    worker_id UUID REFERENCES public.workers(id) NOT NULL,
    rating INTEGER CHECK (rating >= 1 AND rating <= 5) NOT NULL,
    comment TEXT,
    UNIQUE(job_id) -- One feedback per job
);

-- Enable RLS on feedbacks
ALTER TABLE public.feedbacks ENABLE ROW LEVEL SECURITY;

-- Allow Public access for testing (should be refined for production)
CREATE POLICY "Allow All Access on feedbacks" ON public.feedbacks
FOR ALL USING (true);

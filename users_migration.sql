-- Create Users table for OTP-based user authentication
CREATE TABLE IF NOT EXISTS public.users (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    phone TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    last_login TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now())
);

-- Index for fast phone-based lookup
CREATE INDEX IF NOT EXISTS idx_users_phone ON public.users(phone);

-- Enable Row Level Security
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

-- Policy: Allow public read (for login lookup)
CREATE POLICY "Allow Public Read on users" ON public.users
FOR SELECT USING (true);

-- Policy: Allow public insert (for new user registration via OTP)
CREATE POLICY "Allow Public Insert on users" ON public.users
FOR INSERT WITH CHECK (true);

-- Policy: Allow public update (for updating last_login)
CREATE POLICY "Allow Public Update on users" ON public.users
FOR UPDATE USING (true);

-- Migration 002: company details
-- Adds gstin, owner_name, number and metadata to companies.
-- Safe to run on your existing database — every statement is
-- "add column if not exists", so it only adds what's missing.
--
-- Run in Supabase Dashboard -> SQL Editor.

alter table public.companies add column if not exists gstin      text;
alter table public.companies add column if not exists owner_name text;
alter table public.companies add column if not exists number     text;
alter table public.companies add column if not exists metadata   jsonb not null default '{}'::jsonb;

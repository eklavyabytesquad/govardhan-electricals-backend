-- Migration 003: track who created and who last updated a company.
-- created_by already existed; this adds updated_by so edits (once an
-- "edit company" endpoint exists) can be attributed to a user, the same way
-- created_by attributes the company's creation.
-- Safe to run on your existing database.
--
-- Run in Supabase Dashboard -> SQL Editor.

alter table public.companies
    add column if not exists updated_by uuid references public.users(id) on delete set null;

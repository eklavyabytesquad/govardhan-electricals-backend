-- Each company picks one bill/PDF layout to print vouchers with — see
-- lib/pdfTemplates/template1.js, template2.js (frontend). Stored as a plain
-- text id rather than an enum so a new template file can be added later
-- without a migration; the allowed-values list below lives in the backend
-- (services/company_service.py: VALID_PDF_TEMPLATES) which the API enforces.
alter table companies
  add column if not exists pdf_template text not null default 'template1';

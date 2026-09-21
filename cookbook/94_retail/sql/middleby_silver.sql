-- Middleby RFQ demo: the four "dc_*" source extracts as typed silver tables.
--
-- The source tables (schema `source`) hold the CSV rows as text, exactly as delivered. These
-- statements type them, keep every row, and say in column comments what the harvest should
-- know -- the semantic layer reads comments, and the KPI suggestion agent reads the harvest.
-- Known oddities of the extract are stated, not fixed: a quote's `quote_month` does not always
-- agree with `quote_date`, and `quote_status` does not always agree with `pipeline_stage`.
-- Synthetic data (generated 2026-09-10); dates run into the future.

DROP TABLE IF EXISTS silver.dc_rfq_quotes;
CREATE TABLE silver.dc_rfq_quotes AS
SELECT
    CAST(quote_id AS integer)                      AS quote_id,
    CAST(quote_date AS date)                       AS quote_date,
    quote_month,
    CAST(customer_id AS integer)                   AS customer_id,
    customer_name,
    brand,
    pim_category,
    product_sku,
    product_name,
    sales_rep_territory,
    sales_rep_name,
    customer_state,
    customer_segment,
    audience_channel,
    quote_source,
    CAST(quantity AS integer)                      AS quantity,
    CAST(deal_value_usd AS numeric(14, 2))         AS deal_value_usd,
    quote_status,
    pipeline_stage,
    CAST(NULLIF(days_open_as_of_20260701, '') AS integer) AS days_open_as_of_20260701,
    CAST(NULLIF(closed_date, '') AS date)          AS closed_date,
    CAST(NULLIF(days_to_close, '') AS integer)     AS days_to_close,
    NULLIF(lost_reason, '')                        AS lost_reason,
    CAST(live_chat_buy_inquiry_flag AS boolean)    AS live_chat_buy_inquiry_flag,
    po_reference,
    CAST(NULLIF(linked_session_id, '') AS integer) AS linked_session_id,
    CAST(NULLIF(linked_chat_id, '') AS integer)    AS linked_chat_id,
    source_system
FROM source.dc_rfq_quotes;
ALTER TABLE silver.dc_rfq_quotes ADD PRIMARY KEY (quote_id);
COMMENT ON TABLE  silver.dc_rfq_quotes IS 'One row per quote request (RFQ) through the Middleby quote pipeline, from submission to closed-won or closed-lost. The fact table for RFQ and quote pipeline performance.';
COMMENT ON COLUMN silver.dc_rfq_quotes.quote_id IS 'Quote identifier; primary key.';
COMMENT ON COLUMN silver.dc_rfq_quotes.quote_date IS 'The date the quote was submitted. The business date for counting quotes.';
COMMENT ON COLUMN silver.dc_rfq_quotes.quote_month IS 'Reporting month as YYYY-MM. Known-inconsistent with quote_date in this extract; prefer quote_date.';
COMMENT ON COLUMN silver.dc_rfq_quotes.customer_id IS 'The customer who requested the quote; joins to silver.dc_customers.customer_id.';
COMMENT ON COLUMN silver.dc_rfq_quotes.brand IS 'Middleby brand quoted (Hyundai, Levi, Cleveland, Bakers Pride, Merrychef, Southbend, ...). A reporting dimension.';
COMMENT ON COLUMN silver.dc_rfq_quotes.pim_category IS 'Product category from the PIM (Ovens, Fryers, Combi Ovens, Steamers, ...). A reporting dimension.';
COMMENT ON COLUMN silver.dc_rfq_quotes.sales_rep_territory IS 'The sales representative''s territory (North India, West India, ...). A reporting dimension.';
COMMENT ON COLUMN silver.dc_rfq_quotes.customer_state IS 'The customer''s state at the time of the quote. A reporting dimension.';
COMMENT ON COLUMN silver.dc_rfq_quotes.customer_segment IS 'Customer segment (Consumer, Regional Chain, Corporate, Foodservice Consultant, ...). A reporting dimension.';
COMMENT ON COLUMN silver.dc_rfq_quotes.audience_channel IS 'Where the quote came in: Shop Middleby, Dealer Portal, Middleby Portal. The audience dimension.';
COMMENT ON COLUMN silver.dc_rfq_quotes.quote_source IS 'How the quote originated: Rep-Initiated, Live Chat, web form, ...';
COMMENT ON COLUMN silver.dc_rfq_quotes.deal_value_usd IS 'Quoted deal value in USD.';
COMMENT ON COLUMN silver.dc_rfq_quotes.quote_status IS 'Quote status: Open, Closed-Won, Closed-Lost. Known-inconsistent with pipeline_stage in this extract; pipeline_stage is the stage the pipeline report uses.';
COMMENT ON COLUMN silver.dc_rfq_quotes.pipeline_stage IS 'Pipeline stage: prospecting stages up to Closed Won / Closed Lost. Closed-won rate = quotes at Closed Won over quotes at Closed Won or Closed Lost.';
COMMENT ON COLUMN silver.dc_rfq_quotes.closed_date IS 'The date the quote closed (won or lost); NULL while open.';
COMMENT ON COLUMN silver.dc_rfq_quotes.days_to_close IS 'Days from quote_date to closed_date; NULL while open.';
COMMENT ON COLUMN silver.dc_rfq_quotes.lost_reason IS 'Why a quote was lost (Constraints Budget, Chosen Competitor, ...); NULL unless lost. The lost-reason breakdown reads this.';
COMMENT ON COLUMN silver.dc_rfq_quotes.live_chat_buy_inquiry_flag IS 'True when a live-chat "can I buy" inquiry preceded the quote (HubSpot intent signal).';
COMMENT ON COLUMN silver.dc_rfq_quotes.linked_session_id IS 'The web session that led to the quote; joins to silver.dc_web_sessions.session_id.';
COMMENT ON COLUMN silver.dc_rfq_quotes.linked_chat_id IS 'The live chat that led to the quote; joins to silver.dc_chat_inquiries.chat_id.';
COMMENT ON COLUMN silver.dc_rfq_quotes.source_system IS 'System of record for the row: HubSpot or SAP Commerce.';

DROP TABLE IF EXISTS silver.dc_customers;
CREATE TABLE silver.dc_customers AS
SELECT
    CAST(customer_id AS integer) AS customer_id,
    customer_name,
    customer_segment,
    customer_state,
    customer_territory
FROM source.dc_customers;
ALTER TABLE silver.dc_customers ADD PRIMARY KEY (customer_id);
COMMENT ON TABLE  silver.dc_customers IS 'One row per customer: segment, state and territory. The customer dimension for quotes, sessions and chats.';
COMMENT ON COLUMN silver.dc_customers.customer_id IS 'Customer identifier; primary key. Referenced by dc_rfq_quotes.customer_id and dc_web_sessions.customer_id.';
COMMENT ON COLUMN silver.dc_customers.customer_segment IS 'Customer segment (Consumer, Regional Chain, Corporate, ...).';
COMMENT ON COLUMN silver.dc_customers.customer_state IS 'The customer''s state.';
COMMENT ON COLUMN silver.dc_customers.customer_territory IS 'The customer''s sales territory (North, South, East, West).';

DROP TABLE IF EXISTS silver.dc_chat_inquiries;
CREATE TABLE silver.dc_chat_inquiries AS
SELECT
    CAST(chat_id AS integer)                     AS chat_id,
    CAST(chat_date AS date)                      AS chat_date,
    chat_month,
    chat_type,
    brand_discussed,
    pim_category_discussed,
    audience_channel,
    CASE converted_to_quote WHEN 'Y' THEN TRUE WHEN 'N' THEN FALSE END AS converted_to_quote,
    CAST(NULLIF(linked_quote_id, '') AS integer) AS linked_quote_id,
    source_system
FROM source.dc_chat_inquiries;
ALTER TABLE silver.dc_chat_inquiries ADD PRIMARY KEY (chat_id);
COMMENT ON TABLE  silver.dc_chat_inquiries IS 'One row per live-chat inquiry (HubSpot): the "can I buy" intent signal at the top of the quote funnel.';
COMMENT ON COLUMN silver.dc_chat_inquiries.chat_date IS 'The date of the chat.';
COMMENT ON COLUMN silver.dc_chat_inquiries.chat_type IS 'Kind of inquiry: Buy / Service, Online Now Buy, ...';
COMMENT ON COLUMN silver.dc_chat_inquiries.brand_discussed IS 'The brand the chat was about.';
COMMENT ON COLUMN silver.dc_chat_inquiries.pim_category_discussed IS 'The PIM category the chat was about.';
COMMENT ON COLUMN silver.dc_chat_inquiries.converted_to_quote IS 'True when the chat led to a quote; linked_quote_id names it.';
COMMENT ON COLUMN silver.dc_chat_inquiries.linked_quote_id IS 'The quote the chat led to; joins to silver.dc_rfq_quotes.quote_id.';

DROP TABLE IF EXISTS silver.dc_web_sessions;
CREATE TABLE silver.dc_web_sessions AS
SELECT
    CAST(session_id AS integer)                  AS session_id,
    CAST(session_date AS date)                   AS session_date,
    session_month,
    traffic_source,
    traffic_medium,
    device_type,
    browser,
    landing_page,
    pim_category_viewed,
    brand_viewed,
    top_pdp_viewed,
    CAST(pages_per_session AS integer)           AS pages_per_session,
    CAST(session_duration_sec AS integer)        AS session_duration_sec,
    CAST(bounce_flag AS boolean)                 AS bounce_flag,
    NULLIF(search_term_used, '')                 AS search_term_used,
    CAST(search_zero_result_flag AS boolean)     AS search_zero_result_flag,
    CASE used_guided_shopping_tool WHEN 'Y' THEN TRUE WHEN 'N' THEN FALSE END AS used_guided_shopping_tool,
    CASE clicked_get_a_quote WHEN 'Y' THEN TRUE WHEN 'N' THEN FALSE END       AS clicked_get_a_quote,
    CASE completed_quote_form WHEN 'Y' THEN TRUE WHEN 'N' THEN FALSE END      AS completed_quote_form,
    CASE converted_to_quote WHEN 'Y' THEN TRUE WHEN 'N' THEN FALSE END        AS converted_to_quote,
    CAST(NULLIF(linked_quote_id, '') AS integer) AS linked_quote_id,
    CAST(NULLIF(customer_id, '') AS integer)     AS customer_id,
    customer_state,
    customer_segment,
    audience_channel,
    new_vs_returning,
    source_system
FROM source.dc_web_sessions;
ALTER TABLE silver.dc_web_sessions ADD PRIMARY KEY (session_id);
COMMENT ON TABLE  silver.dc_web_sessions IS 'One row per web session on the Middleby shop: traffic source, what was viewed, whether a quote was requested. The top of the quote funnel.';
COMMENT ON COLUMN silver.dc_web_sessions.session_date IS 'The date of the session.';
COMMENT ON COLUMN silver.dc_web_sessions.clicked_get_a_quote IS 'True when the session clicked Get a quote.';
COMMENT ON COLUMN silver.dc_web_sessions.completed_quote_form IS 'True when the session completed the quote form.';
COMMENT ON COLUMN silver.dc_web_sessions.converted_to_quote IS 'True when the session led to a quote; linked_quote_id names it.';
COMMENT ON COLUMN silver.dc_web_sessions.linked_quote_id IS 'The quote the session led to; joins to silver.dc_rfq_quotes.quote_id.';
COMMENT ON COLUMN silver.dc_web_sessions.customer_id IS 'The customer, when known; joins to silver.dc_customers.customer_id.';
COMMENT ON COLUMN silver.dc_web_sessions.search_zero_result_flag IS 'True when a site search returned nothing.';

GRANT USAGE ON SCHEMA silver TO egpt_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA silver TO egpt_reader;

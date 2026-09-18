-- ============================================================================
-- AWS RDS PostgreSQL + pgvector Setup Script
-- ============================================================================
-- Prerequisites for AWS RDS PostgreSQL:
-- 1. Engine: PostgreSQL 15.2+ or 16+
-- 2. DB Parameter Group: Ensure 'rds.force_ssl = 1' (optional, recommended)
--    Note: In RDS PostgreSQL 15.2+, the 'vector' extension is supported natively.
-- ============================================================================

-- Step 1: Create Database if not already created
-- (Run this as master user e.g. postgres or dbadmin)
-- CREATE DATABASE slackbot;
-- \c slackbot

-- Step 2: Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Step 3: Verify pgvector installation
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';

-- Step 4: Verify HNSW index operator support
SELECT typname FROM pg_type WHERE typname = 'vector';

-- Step 5: (Optional) If using a dedicated application user:
-- CREATE USER slack_app WITH PASSWORD 'StrongApplicationPasswordHere!';
-- GRANT CONNECT ON DATABASE slackbot TO slack_app;
-- GRANT USAGE ON SCHEMA public TO slack_app;
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO slack_app;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO slack_app;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO slack_app;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO slack_app;

-- Notice:
-- When the Slack AI Agent boots in ECS Fargate, it will automatically connect
-- and execute table/index migrations (conversations, chat_messages, chat_embeddings, HNSW indexes).

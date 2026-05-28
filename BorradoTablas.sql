SET session_replication_role = 'replica';
DELETE FROM knowledge_chunks;
DELETE FROM chat_messages;
DELETE FROM knowledge_documents;
DELETE FROM chat_threads;
SET session_replication_role = 'origin';

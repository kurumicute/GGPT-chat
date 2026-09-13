-- 建立資料庫
CREATE DATABASE IF NOT EXISTS chat_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE chat_db;

-- 使用者
CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(100) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  google_sub VARCHAR(255) NULL,
  email VARCHAR(320) NULL,
  display_name VARCHAR(255) NULL,
  avatar_url VARCHAR(1024) NULL,
  auth_provider VARCHAR(20) NOT NULL DEFAULT 'local',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,

  UNIQUE KEY uq_users_username (username),
  UNIQUE KEY uq_users_google_sub (google_sub),
  UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- 管理員
CREATE TABLE IF NOT EXISTS admin_users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(100) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  last_login_at TIMESTAMP NULL DEFAULT NULL,

  UNIQUE KEY uq_admin_username (username),
  INDEX idx_admin_active (is_active)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- 對話
CREATE TABLE IF NOT EXISTS conversations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  title VARCHAR(255) NOT NULL DEFAULT '新對話',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,

  INDEX idx_conv_user_updated (user_id, updated_at),

  CONSTRAINT fk_conversations_user
    FOREIGN KEY (user_id)
    REFERENCES users(id)
    ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- 對話分享
CREATE TABLE IF NOT EXISTS conversation_shares (
  conversation_id INT NOT NULL PRIMARY KEY,
  share_token CHAR(36) NOT NULL,
  created_by INT NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,

  UNIQUE KEY uq_share_token (share_token),
  INDEX idx_conversation_share_token (share_token, is_active),
  INDEX idx_conversation_share_creator (created_by),

  CONSTRAINT fk_conversation_share_conversation
    FOREIGN KEY (conversation_id)
    REFERENCES conversations(id)
    ON DELETE CASCADE,

  CONSTRAINT fk_conversation_share_creator
    FOREIGN KEY (created_by)
    REFERENCES users(id)
    ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- 共同對話參與者
CREATE TABLE IF NOT EXISTS conversation_collaborators (
  conversation_id INT NOT NULL,
  user_id INT NOT NULL,
  joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (conversation_id, user_id),
  INDEX idx_conversation_collaborator_user (user_id, joined_at),

  CONSTRAINT fk_collaborator_conversation
    FOREIGN KEY (conversation_id)
    REFERENCES conversations(id)
    ON DELETE CASCADE,

  CONSTRAINT fk_collaborator_user
    FOREIGN KEY (user_id)
    REFERENCES users(id)
    ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- 對話訊息
CREATE TABLE IF NOT EXISTS chat (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  conversation_id INT NULL,
  user_message LONGTEXT NOT NULL,
  image_url VARCHAR(2048) NULL,
  attachment_json LONGTEXT NULL,
  reasoning_summary LONGTEXT NULL,
  bot_reply LONGTEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

  INDEX idx_chat_user (user_id),
  INDEX idx_chat_conv (conversation_id),
  INDEX idx_chat_created (created_at),

  CONSTRAINT fk_chat_user
    FOREIGN KEY (user_id)
    REFERENCES users(id)
    ON DELETE CASCADE,

  CONSTRAINT fk_chat_conversation
    FOREIGN KEY (conversation_id)
    REFERENCES conversations(id)
    ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- Token 與費用統計
CREATE TABLE IF NOT EXISTS token_usage (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  conversation_id INT NULL,
  model VARCHAR(100) NOT NULL,
  input_tokens BIGINT NOT NULL DEFAULT 0,
  cached_input_tokens BIGINT NOT NULL DEFAULT 0,
  output_tokens BIGINT NOT NULL DEFAULT 0,
  reasoning_tokens BIGINT NOT NULL DEFAULT 0,
  total_tokens BIGINT NOT NULL DEFAULT 0,
  total_cost DECIMAL(18,8) NOT NULL DEFAULT 0,
  reasoning_cost DECIMAL(18,8) NOT NULL DEFAULT 0,
  web_search TINYINT(1) NOT NULL DEFAULT 0,
  web_search_calls SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  web_search_cost DECIMAL(18,8) NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

  INDEX idx_usage_user_time (user_id, created_at),
  INDEX idx_usage_user_model (user_id, model),
  INDEX idx_usage_conversation (conversation_id),

  CONSTRAINT fk_token_usage_user
    FOREIGN KEY (user_id)
    REFERENCES users(id)
    ON DELETE CASCADE,

  CONSTRAINT fk_token_usage_conversation
    FOREIGN KEY (conversation_id)
    REFERENCES conversations(id)
    ON DELETE SET NULL
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- 訪客統計
CREATE TABLE IF NOT EXISTS visitor_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  visitor_hash CHAR(64) NOT NULL,
  path VARCHAR(255) NOT NULL,
  referrer VARCHAR(1024) NULL,
  user_agent VARCHAR(1024) NULL,
  device_type VARCHAR(30) NOT NULL DEFAULT 'unknown',
  browser VARCHAR(50) NOT NULL DEFAULT 'unknown',
  os VARCHAR(50) NOT NULL DEFAULT 'unknown',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

  INDEX idx_visitor_time (created_at),
  INDEX idx_visitor_hash_time (visitor_hash, created_at),
  INDEX idx_visitor_path_time (path, created_at)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- 全域聊天室
CREATE TABLE IF NOT EXISTS global_chat_messages (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  message TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

  INDEX idx_global_chat_id (id),
  INDEX idx_global_chat_user (user_id),
  INDEX idx_global_chat_created (created_at),

  CONSTRAINT fk_global_chat_user
    FOREIGN KEY (user_id)
    REFERENCES users(id)
    ON DELETE CASCADE
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;


-- 查看建立結果
SHOW TABLES;
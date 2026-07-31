from django.db import migrations


class Migration(migrations.Migration):
    # The legend tables existed before this app began tracking migrations.
    # This migration intentionally adds only the new category-share tables.
    initial = True
    dependencies = [
        ("users", "0004_ensure_initial_admin"),
    ]

    operations = [
        # Existing legend tables were originally provisioned with syncdb and
        # have no migration state.  Recreate that legacy schema here with
        # idempotent SQL so a fresh test/deployment database gets the full
        # legend feature, while existing deployments remain untouched.
        migrations.RunSQL(
            """
            CREATE TABLE IF NOT EXISTS tb_legend_category (
              id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
              create_time DATETIME(6) NOT NULL,
              update_time DATETIME(6) NOT NULL,
              is_delete BOOL NOT NULL DEFAULT FALSE,
              owner_id BIGINT NULL,
              name VARCHAR(80) NOT NULL,
              is_system BOOL NOT NULL DEFAULT FALSE,
              sort_order INT UNSIGNED NOT NULL DEFAULT 0,
              INDEX tb_legend_category_owner_id (owner_id),
              INDEX tb_legend_category_sort_order (sort_order)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            "DROP TABLE IF EXISTS tb_legend_category;",
        ),
        migrations.RunSQL(
            """
            CREATE TABLE IF NOT EXISTS tb_legend (
              id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
              create_time DATETIME(6) NOT NULL,
              update_time DATETIME(6) NOT NULL,
              is_delete BOOL NOT NULL DEFAULT FALSE,
              owner_id BIGINT NULL,
              category_id BIGINT NOT NULL,
              name VARCHAR(120) NOT NULL,
              original_filename VARCHAR(255) NOT NULL,
              content_type VARCHAR(100) NOT NULL,
              source_file LONGBLOB NOT NULL,
              source_size BIGINT UNSIGNED NOT NULL DEFAULT 0,
              parsed_data JSON NOT NULL,
              preview_svg LONGTEXT NOT NULL,
              source_type VARCHAR(20) NOT NULL,
              is_system BOOL NOT NULL DEFAULT FALSE,
              source_legend_id BIGINT NULL,
              INDEX tb_legend_owner_id (owner_id),
              INDEX tb_legend_category_id (category_id),
              INDEX tb_legend_source_legend_id (source_legend_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            "DROP TABLE IF EXISTS tb_legend;",
        ),
        migrations.RunSQL(
            """
            CREATE TABLE IF NOT EXISTS tb_legend_share (
              id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
              create_time DATETIME(6) NOT NULL,
              update_time DATETIME(6) NOT NULL,
              is_delete BOOL NOT NULL DEFAULT FALSE,
              legend_id BIGINT NOT NULL,
              creator_id BIGINT NOT NULL,
              code VARCHAR(24) NOT NULL UNIQUE,
              expires_at DATETIME(6) NOT NULL,
              max_uses INT UNSIGNED NULL,
              used_count INT UNSIGNED NOT NULL DEFAULT 0,
              is_revoked BOOL NOT NULL DEFAULT FALSE,
              INDEX tb_legend_share_legend_id (legend_id),
              INDEX tb_legend_share_creator_id (creator_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            "DROP TABLE IF EXISTS tb_legend_share;",
        ),
        migrations.RunSQL(
            """
            CREATE TABLE IF NOT EXISTS tb_legend_share_redemption (
              id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
              create_time DATETIME(6) NOT NULL,
              update_time DATETIME(6) NOT NULL,
              is_delete BOOL NOT NULL DEFAULT FALSE,
              share_id BIGINT NOT NULL,
              recipient_id BIGINT NOT NULL,
              copied_legend_id BIGINT NOT NULL UNIQUE,
              UNIQUE KEY uniq_legend_share_recipient (share_id, recipient_id),
              INDEX tb_legend_share_redemption_share_id (share_id),
              INDEX tb_legend_share_redemption_recipient_id (recipient_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            "DROP TABLE IF EXISTS tb_legend_share_redemption;",
        ),
        # Use idempotent SQL so this upgrade works for deployments that
        # already provisioned the preceding legacy tables with syncdb.
        migrations.RunSQL(
            """
            CREATE TABLE IF NOT EXISTS tb_legend_category_share (
              id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
              create_time DATETIME(6) NOT NULL,
              update_time DATETIME(6) NOT NULL,
              is_delete BOOL NOT NULL DEFAULT FALSE,
              category_id BIGINT NOT NULL,
              creator_id BIGINT NOT NULL,
              code VARCHAR(24) NOT NULL UNIQUE,
              expires_at DATETIME(6) NOT NULL,
              max_uses INT NULL,
              used_count INT NOT NULL DEFAULT 0,
              is_revoked BOOL NOT NULL DEFAULT FALSE,
              INDEX tb_legend_category_share_category_id (category_id),
              INDEX tb_legend_category_share_creator_id (creator_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            "DROP TABLE IF EXISTS tb_legend_category_share;",
        ),
        migrations.RunSQL(
            """
            CREATE TABLE IF NOT EXISTS tb_legend_category_share_redemption (
              id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
              create_time DATETIME(6) NOT NULL,
              update_time DATETIME(6) NOT NULL,
              is_delete BOOL NOT NULL DEFAULT FALSE,
              share_id BIGINT NOT NULL,
              recipient_id BIGINT NOT NULL,
              copied_category_id BIGINT NOT NULL UNIQUE,
              UNIQUE KEY uniq_legend_category_share_recipient (share_id, recipient_id),
              INDEX tb_legend_category_share_redemption_share_id (share_id),
              INDEX tb_legend_category_share_redemption_recipient_id (recipient_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            "DROP TABLE IF EXISTS tb_legend_category_share_redemption;",
        ),
    ]

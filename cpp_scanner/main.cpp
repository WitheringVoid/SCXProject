#include <iostream>
#include <thread>
#include <chrono>
#include <string>
#include <vector>
#include <mutex>
#include <sqlite3.h>
#include <curl/curl.h>
#include <nlohmann/json.hpp>
#include <ctime>
#include <windows.h>
#include <locale.h>

using json = nlohmann::json;

const std::string QUEUE_DB = "queue.db";
const std::string CHARS_DB = "characters.db";
const std::string TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiIyMDMwIiwianRpIjoiYTUzMjRkYzM3NzQzNGNiY2JlYjI2YmRlYjFiMjA4MWYzMDEzNjMyYzM0ZTYwZDc1N2U5ZDlkZmYzMjg3YzVmODliMWIzZTMxODQyMTU2OGUiLCJpYXQiOjE3NzQ2MTU3NjIuNDU3OTk0LCJuYmYiOjE3NzQ2MTU3NjIuNDU3OTk1LCJleHAiOjE4MDYxNTE3NjIuNDQ5MTg2LCJzdWIiOiIiLCJzY29wZXMiOltdfQ.PHbDR8O3I362pzqWpZrlpc2vUgikClYeM8nVx6xiJ8ptoIpSgp_24IdPIYGa9O-pBNLCdv6tVMev-LvnTNRvMIvP_hd9hAgIaZGh_QGaGKt8sA9FwZoPVoAMTurS3Xr9bZ_dG9oFtlCrSA_yOam4t6DDDvZhNPJNLYIHQY69sn7SRA5m-C41Dlnu4Q4OXtenC1mYO-X2_unvqIOEZJO0_05ttodrLxjDcJYm621EkZzbnoisU35P49rR_WPt8v97igfK1lu-UcxVvTWKVsfnbgf4vX6gNvKAOjYhouEckGmL7BFgcmLil82L8g5BzVbMyGW84v2sIW5ypEqPy9VC2t4Qa6ajcaR7B4Zlyf50azLueTSDt0nqe2GyKQBUslqwMEdh6qMn5jKs6TXcnCbdCe1juP-MTEuowWOPYGGNpsbaMN_E72vgbRtv4XxGqENvt3v2ZZVLWiN3U4xMw9Bz7Ky6NfAo3AzrQdRq3Rs70jShKgN3CcG_g5YjcHpzegsaEiKwi9PZJPaAYGUUYRh08FEBo0GvKBzr3H7VsS1hgL4TdA8A66kCZ0xPyzwG9exu5udn0sM9_TEtceeWmn-nsaTfvGi-HWwdSoZEy4j5DypjNcGq2AuI4tdteKhKlrjVBmVafrYivlxkZDfzXP0tOXZupS2KLTZHylGxADmIHnA";

std::mutex queue_db_mutex;
std::mutex chars_db_mutex;

struct PlayerRow {
    std::string nickname;
    std::string region;
    std::string created_at;
};

struct PlayerStats {
    int kills = 0;
    int deaths = 0;
    int hours_played = 0;
    double kd = 0.0;
    std::string last_online = "не найден";
};

std::string now_string() {
    time_t now = time(0);
    tm* ltm = localtime(&now);
    char buf[32];
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", ltm);
    return std::string(buf);
}

void exec_simple(sqlite3* db, const char* sql) {
    char* err = nullptr;
    sqlite3_exec(db, sql, 0, 0, &err);
    if (err) {
        sqlite3_free(err);
    }
}

void init_queue_db() {
    std::lock_guard<std::mutex> lock(queue_db_mutex);

    sqlite3* db = nullptr;
    if (sqlite3_open(QUEUE_DB.c_str(), &db) != SQLITE_OK) {
        if (db) sqlite3_close(db);
        return;
    }

    exec_simple(db, "PRAGMA journal_mode=WAL;");
    exec_simple(db, "PRAGMA synchronous=NORMAL;");
    exec_simple(db, "PRAGMA busy_timeout=5000;");

    const char* sql =
        "CREATE TABLE IF NOT EXISTS player_queue ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "nickname TEXT NOT NULL,"
        "region TEXT NOT NULL,"
        "created_at TEXT NOT NULL,"
        "UNIQUE(nickname, region)"
        ");";

    exec_simple(db, sql);
    sqlite3_close(db);
}

void init_chars_db() {
    std::lock_guard<std::mutex> lock(chars_db_mutex);

    sqlite3* db = nullptr;
    if (sqlite3_open(CHARS_DB.c_str(), &db) != SQLITE_OK) {
        if (db) sqlite3_close(db);
        return;
    }

    exec_simple(db, "PRAGMA journal_mode=WAL;");
    exec_simple(db, "PRAGMA synchronous=NORMAL;");
    exec_simple(db, "PRAGMA busy_timeout=5000;");

    const char* sql =
        "CREATE TABLE IF NOT EXISTS players ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "nickname TEXT NOT NULL,"
        "region TEXT NOT NULL,"
        "kills INTEGER DEFAULT 0,"
        "deaths INTEGER DEFAULT 0,"
        "hours_played INTEGER DEFAULT 0,"
        "kd REAL DEFAULT 0,"
        "last_online TEXT DEFAULT 'не найден',"
        "created_at TEXT NOT NULL,"
        "updated_at TEXT,"
        "UNIQUE(nickname, region)"
        ");";

    exec_simple(db, sql);
    sqlite3_close(db);
}

std::vector<PlayerRow> get_rows_for_update() {
    std::lock_guard<std::mutex> lock(queue_db_mutex);

    std::vector<PlayerRow> rows;

    sqlite3* db = nullptr;
    if (sqlite3_open(QUEUE_DB.c_str(), &db) != SQLITE_OK) {
        if (db) sqlite3_close(db);
        return rows;
    }

    exec_simple(db, "PRAGMA busy_timeout=5000;");

    const char* sql = "SELECT nickname, region, created_at FROM player_queue;";
    sqlite3_stmt* stmt = nullptr;

    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        sqlite3_close(db);
        return rows;
    }

    while (sqlite3_step(stmt) == SQLITE_ROW) {
        PlayerRow row;

        const unsigned char* nick = sqlite3_column_text(stmt, 0);
        const unsigned char* reg = sqlite3_column_text(stmt, 1);
        const unsigned char* created = sqlite3_column_text(stmt, 2);

        row.nickname = nick ? reinterpret_cast<const char*>(nick) : "";
        row.region = reg ? reinterpret_cast<const char*>(reg) : "RU";
        row.created_at = created ? reinterpret_cast<const char*>(created) : now_string();

        if (!row.nickname.empty()) {
            rows.push_back(row);
        }
    }

    sqlite3_finalize(stmt);
    sqlite3_close(db);
    return rows;
}

void upsert_player_stats(
    const std::string& nickname,
    const std::string& region,
    const std::string& created_at,
    const PlayerStats& stats
) {
    std::lock_guard<std::mutex> lock(chars_db_mutex);

    sqlite3* db = nullptr;
    if (sqlite3_open(CHARS_DB.c_str(), &db) != SQLITE_OK) {
        if (db) sqlite3_close(db);
        return;
    }

    exec_simple(db, "PRAGMA busy_timeout=5000;");

    const char* sql =
        "INSERT INTO players (nickname, region, kills, deaths, hours_played, kd, last_online, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(nickname, region) DO UPDATE SET "
        "kills = excluded.kills, "
        "deaths = excluded.deaths, "
        "hours_played = excluded.hours_played, "
        "kd = excluded.kd, "
        "last_online = excluded.last_online, "
        "updated_at = excluded.updated_at;";

    sqlite3_stmt* stmt = nullptr;

    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        sqlite3_close(db);
        return;
    }

    std::string now = now_string();

    sqlite3_bind_text(stmt, 1, nickname.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 2, region.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(stmt, 3, stats.kills);
    sqlite3_bind_int(stmt, 4, stats.deaths);
    sqlite3_bind_int(stmt, 5, stats.hours_played);
    sqlite3_bind_double(stmt, 6, stats.kd);
    sqlite3_bind_text(stmt, 7, stats.last_online.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 8, created_at.c_str(), -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(stmt, 9, now.c_str(), -1, SQLITE_TRANSIENT);

    sqlite3_step(stmt);
    sqlite3_finalize(stmt);
    sqlite3_close(db);
}

void set_not_found(const std::string& nickname, const std::string& region, const std::string& created_at) {
    PlayerStats stats;
    stats.kills = 0;
    stats.deaths = 0;
    stats.hours_played = 0;
    stats.kd = 0.0;
    stats.last_online = "не найден";
    upsert_player_stats(nickname, region, created_at, stats);
}

size_t write_callback(void* contents, size_t size, size_t nmemb, void* userp) {
    ((std::string*)userp)->append((char*)contents, size * nmemb);
    return size * nmemb;
}

std::string format_last_login(const std::string& iso) {
    if (iso.empty()) return "не найден";
    if (iso.size() < 16) return iso;

    std::string year = iso.substr(0, 4);
    std::string month = iso.substr(5, 2);
    std::string day = iso.substr(8, 2);
    std::string hour = iso.substr(11, 2);
    std::string minute = iso.substr(14, 2);

    return day + "." + month + "." + year + ", " + hour + ":" + minute;
}

bool load_player_from_api(const std::string& nickname, const std::string& region, PlayerStats& stats) {
    CURL* curl = curl_easy_init();
    if (!curl) return false;

    std::string response;
    std::string url = "https://eapi.stalcraft.net/" + region + "/character/by-name/" + nickname + "/profile";

    struct curl_slist* headers = nullptr;
    std::string auth = "Authorization: Bearer " + TOKEN;
    headers = curl_slist_append(headers, auth.c_str());

    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_callback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 20L);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);

    CURLcode res = curl_easy_perform(curl);

    long code = 0;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &code);

    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);

    if (res != CURLE_OK || code != 200) {
        return false;
    }

    try {
        json data = json::parse(response);

        int kills = 0;
        int deaths = 0;
        int hours = 0;
        std::string last_online = "не найден";

        if (data.contains("lastLogin") && data["lastLogin"].is_string()) {
            last_online = format_last_login(data["lastLogin"].get<std::string>());
        }

        if (data.contains("stats") && data["stats"].is_array()) {
            for (auto& item : data["stats"]) {
                if (!item.contains("id") || !item.contains("value")) {
                    continue;
                }

                std::string id = item["id"].get<std::string>();

                if (id == "kil") {
                    kills = item["value"].get<int>();
                } else if (id == "dea") {
                    deaths = item["value"].get<int>();
                } else if (id == "pla-tim") {
                    long long ms = item["value"].get<long long>();
                    hours = static_cast<int>(ms / 1000 / 60 / 60);
                }
            }
        }

        stats.kills = kills;
        stats.deaths = deaths;
        stats.hours_played = hours;
        stats.kd = deaths > 0 ? static_cast<double>(kills) / static_cast<double>(deaths) : 0.0;
        stats.last_online = last_online;

        return true;
    } catch (...) {
        return false;
    }
}

int main() {
    setlocale(LC_ALL, ".UTF-8");
    SetConsoleCP(65001);
    SetConsoleOutputCP(65001);

    curl_global_init(CURL_GLOBAL_DEFAULT);

    init_queue_db();
    init_chars_db();

    while (true) {
        std::cout << "жду 5 секунд..." << std::endl;
        std::this_thread::sleep_for(std::chrono::seconds(5));

        std::vector<PlayerRow> rows = get_rows_for_update();

        for (auto& row : rows) {
            std::cout << "проверяю: " << row.nickname << " " << row.region << std::endl;

            PlayerStats stats;
            bool ok = load_player_from_api(row.nickname, row.region, stats);

            if (ok) {
                upsert_player_stats(row.nickname, row.region, row.created_at, stats);
                std::cout << "обновлено: " << row.nickname << std::endl;
            } else {
                set_not_found(row.nickname, row.region, row.created_at);
                std::cout << "не найдено или ошибка: " << row.nickname << std::endl;
            }
        }
    }

    curl_global_cleanup();
    return 0;
}
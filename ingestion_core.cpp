#include <iostream>
#include <string>
#include <cstring>
#include <vector>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <thread>
#include <curl/curl.h>
#include "json.hpp"
#include "localization.h"   // <-- the real math now lives in the pipeline

using json = nlohmann::json;

#define UDP_PORT 5005
#define BUFFER_SIZE 8192    // bigger: each victim now carries 6 anchor distances

// ---------------------------------------------------------------------------
//  Turn ONE incoming victim (vitals + a list of anchor distances) into an
//  outgoing victim (vitals + a COMPUTED x,y,z position).
//
//  This is the C++ node's real job. It reads the distances each beacon/drone
//  measured, runs multilateration via localize(), and produces a position
//  nobody typed by hand.
// ---------------------------------------------------------------------------
json process_victim(const json& v) {
    std::vector<Anchor> anchors;
    for (const auto& a : v.at("anchors")) {
        anchors.push_back(Anchor{
            a.at("x").get<double>(),
            a.at("y").get<double>(),
            a.at("z").get<double>(),
            a.at("range").get<double>(),
            a.at("is_master").get<bool>()
        });
    }

    Position p = localize(anchors);

    json out;
    out["victim_id"]  = v.at("victim_id");
    out["bpm"]        = v.at("bpm");
    out["spo2"]       = v.at("spo2");
    out["hemorrhage"] = v.at("hemorrhage");
    out["solved"]     = p.solved;
    // Round to 2 decimals so the JSON stays tidy.
    out["x"] = std::round(p.x * 100.0) / 100.0;
    out["y"] = std::round(p.y * 100.0) / 100.0;
    out["z"] = std::round(p.z * 100.0) / 100.0;
    return out;
}

// ---- HTTP bridge to the Python triage API (unchanged) ----
void send_to_python(const std::string& json_payload) {
    CURL* curl = curl_easy_init();
    if (curl) {
        struct curl_slist* headers = NULL;
        headers = curl_slist_append(headers, "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_URL, "http://127.0.0.1:8000/telemetry/sync");
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, json_payload.c_str());
        CURLcode res = curl_easy_perform(curl);
        if (res != CURLE_OK)
            std::cerr << "HTTP POST failed: " << curl_easy_strerror(res) << std::endl;
        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);
    }
}

int main() {
    int sockfd;
    struct sockaddr_in server_addr, client_addr;
    char buffer[BUFFER_SIZE];

    if ((sockfd = socket(AF_INET, SOCK_DGRAM, 0)) < 0) {
        std::cerr << "Socket creation failed!" << std::endl;
        return -1;
    }

    memset(&server_addr, 0, sizeof(server_addr));
    memset(&client_addr, 0, sizeof(client_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = INADDR_ANY;
    server_addr.sin_port = htons(UDP_PORT);

    if (bind(sockfd, (const struct sockaddr*)&server_addr, sizeof(server_addr)) < 0) {
        std::cerr << "Bind failed! Is port 5005 already in use?" << std::endl;
        return -1;
    }

    curl_global_init(CURL_GLOBAL_ALL);
    std::cout << "C++ Localization Core booted. Listening on UDP " << UDP_PORT << "..." << std::endl;

    socklen_t len = sizeof(client_addr);
    while (true) {
        int n = recvfrom(sockfd, (char*)buffer, BUFFER_SIZE - 1,
                         MSG_WAITALL, (struct sockaddr*)&client_addr, &len);
        if (n <= 0) continue;
        buffer[n] = '\0';

        try {
            json incoming = json::parse(buffer);
            json outgoing;
            outgoing["victims"]  = json::array();
            outgoing["rescuers"] = incoming.value("rescuers", json::array());

            for (const auto& v : incoming["victims"]) {
                json solved = process_victim(v);
                std::cout << "[LOCATED] " << solved["victim_id"].get<std::string>()
                          << "  ->  (" << solved["x"] << ", " << solved["y"]
                          << ", " << solved["z"] << ")"
                          << (solved["solved"].get<bool>() ? "" : "  [UNSOLVED]")
                          << std::endl;
                outgoing["victims"].push_back(solved);
            }

            std::string payload_str = outgoing.dump();
            std::thread([payload_str]() { send_to_python(payload_str); }).detach();
        }
        catch (json::exception& e) {
            std::cerr << "JSON error: " << e.what() << std::endl;
        }
    }

    close(sockfd);
    curl_global_cleanup();
    return 0;
}

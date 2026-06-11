#ifndef DATA_CONTRACTS_H
#define DATA_CONTRACTS_H

#include <string>
#include <cstdint>

// This is what the raw binary would look like over the air in a real scenario
struct __attribute__((packed)) RawTelemetryPacket {
    char victim_id[16];   // Fixed-size UUID
    uint8_t bpm;          // 0-255 is plenty for heart rate
    uint8_t spo2;         // 0-100 percentage
    float depth;          // Z-axis from drone mapping
    uint16_t x;           // Grid X coordinate
    uint16_t y;           // Grid Y coordinate
    float radius;         // Signal confidence radius
    bool hemorrhage;      // Manual or derived critical flag
};

// You can expand this later for your Dijkstra grid routing
struct GridCell {
    float elevation;
    bool is_obstacle;
    bool has_victim;
};

#endif
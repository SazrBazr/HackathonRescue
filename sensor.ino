#include <Wire.h>
#include <MAX3010x.h>
#include <Adafruit_SSD1306.h>
#include "filters.h"
#include <WiFi.h>
#include <WiFiUdp.h>

// --- WIFI CREDENTIALS (CHANGE THESE) ---
const char* ssid = "x";
const char* password = "x";

// -> Look for: "Wireless LAN adapter WiFi:"
//    -> Scroll down to: "IPv4 Address"
//       -> Grab this number: x
const char* hostIP = "x";

const int udpPort = 5007;
WiFiUDP udp;
IPAddress serverIP; // Safer way to route packets
// ---------------------------------------

#define SCREEN_WIDTH 128 
#define SCREEN_HEIGHT 64 
#define OLED_RESET    -1 
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

MAX30105 sensor;
const auto kSamplingRate = sensor.SAMPLING_RATE_400SPS;
const float kSamplingFrequency = 400.0;

const unsigned long kFingerThreshold = 10000;
const unsigned int kFingerCooldownMs = 500;
const float kEdgeThreshold = -2000.0;
const float kLowPassCutoff = 5.0;
const float kHighPassCutoff = 0.5;

const bool kEnableAveraging = true;
const int kAveragingSamples = 5; 
const int kSampleThreshold = 5;

LowPassFilter low_pass_filter_red(kLowPassCutoff, kSamplingFrequency);
LowPassFilter low_pass_filter_ir(kLowPassCutoff, kSamplingFrequency);
HighPassFilter high_pass_filter(kHighPassCutoff, kSamplingFrequency);
Differentiator differentiator(kSamplingFrequency);
MovingAverageFilter<kAveragingSamples> averager_bpm;
MovingAverageFilter<kAveragingSamples> averager_r;
MovingAverageFilter<kAveragingSamples> averager_spo2;

MinMaxAvgStatistic stat_red;
MinMaxAvgStatistic stat_ir;

float kSpO2_A = 1.5958422;
float kSpO2_B = -34.6596622;
float kSpO2_C = 112.6898759;

long last_heartbeat = 0;
long finger_timestamp = 0;
bool finger_detected = false;
float last_diff = NAN;
bool crossed = false;
long crossed_time = 0;
bool display_reset = true;

unsigned long lastSendTime = 0;

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);

  // Safely parse the IP address string
  serverIP.fromString(hostIP);

  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected! IP address: ");
  Serial.println(WiFi.localIP());

  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println(F("SSD1306 allocation failed"));
    while (1);
  }

  if(sensor.begin() && sensor.setSamplingRate(kSamplingRate)) { 
    Serial.println("Sensor initialized. Place your finger on the sensor.");
  }
  else {
    Serial.println("Sensor not found. Check wiring!");  
    while(1);
  }

  display.clearDisplay();
  initDrawScreen();
}

void loop() {
  auto sample = sensor.readSample(1000);
  float current_value_red = sample.red;
  float current_value_ir = sample.ir;

  if(sample.red > kFingerThreshold) {
    if(millis() - finger_timestamp > kFingerCooldownMs) {
      if (!finger_detected) Serial.println("Finger placed. Calculating...");
      finger_detected = true;
    }
  } else {
    if (finger_detected == true) Serial.println("--- No Finger Detected ---");
    
    // Send ZEROS to python when finger is off so it triggers the 30s timer
    if (millis() - lastSendTime > 2000) {
      if (WiFi.status() == WL_CONNECTED) {
        udp.beginPacket(serverIP, udpPort);
        udp.print("0,0");
        udp.endPacket();
        Serial.println(">> Zeroes sent (No finger)");
      } else {
        WiFi.reconnect(); // Aggressively fix dropped WiFi
      }
      lastSendTime = millis();
    }

    differentiator.reset(); averager_bpm.reset(); averager_r.reset(); averager_spo2.reset();
    low_pass_filter_red.reset(); low_pass_filter_ir.reset(); high_pass_filter.reset();
    stat_red.reset(); stat_ir.reset();
    finger_detected = false; finger_timestamp = millis();
  }

  if(finger_detected) {
    displayMeasuredValues(false, 0, 0);
    current_value_red = low_pass_filter_red.process(current_value_red);
    current_value_ir = low_pass_filter_ir.process(current_value_ir);
    stat_red.process(current_value_red); stat_ir.process(current_value_ir);

    float current_value = high_pass_filter.process(current_value_red);
    float current_diff = differentiator.process(current_value);

    if(!isnan(current_diff) && !isnan(last_diff)) {
      if(last_diff > 0 && current_diff < 0) { crossed = true; crossed_time = millis(); }
      if(current_diff > 0) { crossed = false; }
  
      if(crossed && current_diff < kEdgeThreshold) {
        if(last_heartbeat != 0 && crossed_time - last_heartbeat > 300) {
          int bpm = 60000/(crossed_time - last_heartbeat);
          float rred = (stat_red.maximum()-stat_red.minimum())/stat_red.average();
          float rir = (stat_ir.maximum()-stat_ir.minimum())/stat_ir.average();
          float r = rred/rir;
          float spo2 = kSpO2_A * r * r + kSpO2_B * r + kSpO2_C;
          
          if (spo2 > 100.0) spo2 = 100.0;
          else if (spo2 < 50.0) spo2 = 50.0; 
          
          if(bpm > 50 && bpm < 250) {
            if(kEnableAveraging) {
              int average_bpm = averager_bpm.process(bpm);
              int average_r = averager_r.process(r);
              int average_spo2 = averager_spo2.process(spo2);
  
              if(averager_bpm.count() >= kSampleThreshold) {
                // --- SEND LIVE VITALS TO PYTHON VIA UDP ---
                if (millis() - lastSendTime > 1500) { 
                  if(WiFi.status() == WL_CONNECTED) {
                    udp.beginPacket(serverIP, udpPort);
                    udp.print(average_bpm);
                    udp.print(",");
                    udp.print(average_spo2);
                    udp.endPacket();
                    Serial.println(">> Packet sent to Python Bridge!");
                  } else {
                    Serial.println("!! WiFi Disconnected !! Reconnecting...");
                    WiFi.reconnect();
                  }
                  lastSendTime = millis();
                }

                displayMeasuredValues(false, average_bpm, average_spo2);
              }
            } else {
              displayMeasuredValues(false, bpm, spo2);
            }
          }
          stat_red.reset(); stat_ir.reset();
        }
        crossed = false; last_heartbeat = crossed_time;
      }
    }
    last_diff = current_diff;
  } else {
    displayMeasuredValues(true, 0, 0);
  }
}

void initDrawScreen(void) {
  display.clearDisplay(); display.setTextSize(1); display.setTextColor(WHITE);
  display.setCursor(0,0); display.println(F("  Taste The Code"));
  display.println(F("")); display.setCursor(5, display.getCursorY());
  display.setTextSize(2); display.println(F("BPM  %SpO2"));
  display.display();
}

void displayMeasuredValues(bool no_finger, int32_t beatAvg, int32_t spo2) {
  display.setCursor(5,35); display.setTextColor(WHITE, BLACK);
  if(no_finger) {
    display.setTextSize(2); display.println(F("No Pulse  ")); 
    display.setCursor(5, 50); display.println(F("Detected  "));
    display_reset = true; display.display();
  } else if(beatAvg < 30 && display_reset) {
    display.setTextSize(2); display.println(F("Pls. Wait "));
    display.setCursor(5, 50); display.println(F("          ")); 
    display_reset = false; display.display();
  } else if(beatAvg >= 30) {
    display.setTextSize(2); display.println(F("          ")); 
    display.setCursor(5, 50); display.println(F("          ")); 
    display.setCursor(5,35); display.setTextSize(3); display.print(beatAvg); display.print(F(" "));
    if(spo2 >= 20 && spo2 <= 100) display.print(spo2); else display.print(F("--"));
    display.println(F("  ")); display.display();
  }
}

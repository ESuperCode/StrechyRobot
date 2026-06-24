#include "esp_camera.h"
#include <WiFi.h>

// Exact GPIO pin configurations for Elegoo Smart Car ESP32-S3 Camera board
#define PWDN_GPIO_NUM     -1
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM     40
#define SIOD_GPIO_NUM     17
#define SIOC_GPIO_NUM     18
#define Y9_GPIO_NUM       39
#define Y8_GPIO_NUM       41
#define Y7_GPIO_NUM       42
#define Y6_GPIO_NUM       12
#define Y5_GPIO_NUM       3
#define Y4_GPIO_NUM       14
#define Y3_GPIO_NUM       47
#define Y2_GPIO_NUM       13
#define VSYNC_GPIO_NUM    21
#define HREF_GPIO_NUM     38
#define PCLK_GPIO_NUM     11

// Your local standalone Hotspot network details
const char* ap_ssid = "Elegoo-Cam";
const char* ap_password = "CamPassword123"; // Must be at least 8 characters

// Start standard web server on default port 80
WiFiServer stream_server(80);

void startCameraServer() {
  stream_server.begin();
}

void handleClient() {
  // Check if a client (like your Python script) is trying to connect
  WiFiClient client = stream_server.accept(); 
  
  if (client) {
    // Read the incoming HTTP network request
    String req = client.readStringUntil('\r');
    client.flush();
    
    // Check if the client requested the stream endpoint
    if (req.indexOf("/stream") != -1) {
      // Send proper HTTP multipart MJPEG streaming headers
      client.print("HTTP/1.1 200 OK\r\n");
      client.print("Content-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n");
      
      // Loop indefinitely while Python stays connected
      while (client.connected()) {
        // Grab a frame buffer pointer from the camera sensor hardware
        camera_fb_t * fb = esp_camera_fb_get();
        if (!fb) {
          delay(10);
          continue;
        }
        
        // Write the standard image encapsulation frame wrapper
        client.print("--frame\r\n");
        client.print("Content-Type: image/jpeg\r\n");
        client.print("Content-Length: " + String(fb->len) + "\r\n\r\n");
        
        // Break up the raw binary image matrix bytes and stream them over the socket
        uint8_t *out_buf = fb->buf;
        size_t out_len = fb->len;
        size_t bytes_sent = 0;
        
        while (bytes_sent < out_len) {
          size_t chunk = (out_len - bytes_sent > 1024) ? 1024 : (out_len - bytes_sent);
          client.write(out_buf + bytes_sent, chunk);
          bytes_sent += chunk;
        }
        
        client.print("\r\n");
        
        // Clear frame memory space to make room for the next video snapshot
        esp_camera_fb_return(fb);
        
        // 30ms delay provides stable frame rate pacing
        delay(30); 
      }
    } else {
      // Basic landing page if you open the IP address in a regular web browser
      client.print("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n");
      client.print("<html><body><h1>Camera Server Online!</h1><p>Stream link: <a href='/stream'>/stream</a></p></body></html>");
    }
    client.stop();
  }
}

void setup() {
  // Open serial diagnostics engine at standard speed
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n--- Elegoo ESP32-S3 Camera Booting ---");
  
  // Set up camera framework structure configurations
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  
  // Explicitly mapping SCCB configuration definitions 
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  
  // Default stream parameters: Standard VGA resolution (640x480)
  config.frame_size = FRAMESIZE_VGA; 
  config.jpeg_quality = 12; // 0-63 scale: lower value yields higher clarity but uses more memory         
  config.fb_count = 2; // Allocation of dual-frame ring buffering for fluid throughput

  // Attempt to boot up physical camera peripheral components
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[CRITICAL ERROR] Camera hardware initialization failed: 0x%x\n", err);
    while(true) { delay(1000); } // Stop program execution if physical lens connection fails
  }
  Serial.println("[OK] Camera Sensor Initialized Successfully.");

  // Spin up standalone local Access Point configuration engine
  Serial.print("Deploying SoftAP Local Network... ");
  WiFi.softAP(ap_ssid, ap_password);
  
  // Fetch static gateway IP address (Always defaults to 192.168.4.1 for ESP chips)
  IPAddress IP = WiFi.softAPIP();
  Serial.print("\n[SUCCESS] Hotspot is broadcasting! IP Address to point to: ");
  Serial.println(IP);

  // Open the network port socket listener
  startCameraServer();
  Serial.println("[OK] Video Streaming Application Active and Listening.");
}

void loop() {
  // Continuously scan for and process Python client network connection requests
  handleClient();
}

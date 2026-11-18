// ІВС температурного поля печі полімеризації: ESP32 + 12 × Adafruit 3263 (MAX31856), термопари K.
// Спільна шина SPI: SCK = GPIO18, MISO = GPIO19, MOSI = GPIO23; індивідуальні CS — див. CS[].
// Вихід через USB (115200 бод), 1 рядок на секунду:
//   D;<мс від старту>;T1..T12;CJ1..CJ12;<маска помилок, hex>
// Порядок каналів: П1..П9 (повітря), Лист, Труба, Опора. Формат розбирає dzherela.parse_line().
// Бібліотека: Adafruit MAX31856 (Arduino Library Manager).
// GPIO5 і GPIO15 — strapping-піни ESP32: CS у неактивному стані HIGH, що не заважає завантаженню.

#include <SPI.h>
#include <Adafruit_MAX31856.h>

const int N = 12;
const int CS[N] = {4, 5, 13, 14, 15, 16, 17, 21, 22, 25, 26, 27};
const uint32_t PERIOD_MS = 1000;

Adafruit_MAX31856* tc[N];
bool present[N];

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < N; i++) {          // усі CS у HIGH до ініціалізації шини
    pinMode(CS[i], OUTPUT);
    digitalWrite(CS[i], HIGH);
  }
  SPI.begin(18, 19, 23);
  for (int i = 0; i < N; i++) {
    tc[i] = new Adafruit_MAX31856(CS[i], &SPI);
    present[i] = tc[i]->begin();
    if (present[i]) {
      tc[i]->setThermocoupleType(MAX31856_TCTYPE_K);
      tc[i]->setAveragingMode(MAX31856_AVG_SEL_4);
      tc[i]->setNoiseFilter(MAX31856_NOISE_FILTER_50HZ);
      tc[i]->setConversionMode(MAX31856_CONTINUOUS);
    }
  }
  Serial.print("# IVS MAX31856 x12, present mask: ");
  uint16_t m = 0;
  for (int i = 0; i < N; i++) if (present[i]) m |= (1 << i);
  Serial.println(m, HEX);
}

void loop() {
  static uint32_t next = 0;
  uint32_t now = millis();
  if ((int32_t)(now - next) < 0) return;
  next = now + PERIOD_MS;

  float T[N], CJ[N];
  uint16_t fault = 0;
  for (int i = 0; i < N; i++) {
    if (!present[i]) { T[i] = NAN; CJ[i] = NAN; fault |= (1 << i); continue; }
    T[i] = tc[i]->readThermocoupleTemperature();
    CJ[i] = tc[i]->readCJTemperature();
    if (tc[i]->readFault()) fault |= (1 << i);       // обрив, вихід за діапазон, перенапруга
  }
  Serial.print("D;");
  Serial.print(now);
  for (int i = 0; i < N; i++) { Serial.print(';'); Serial.print(T[i], 4); }
  for (int i = 0; i < N; i++) { Serial.print(';'); Serial.print(CJ[i], 3); }
  Serial.print(';');
  Serial.println(fault, HEX);
}

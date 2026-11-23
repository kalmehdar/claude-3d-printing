// Pressure-advance tower (Klipper's TUNING_TOWER method): a plain 50 mm square, 32 mm tall, sliced with one wall and
// no infill so each layer is a single fast loop with four sharp corners. Workshop adds
// TUNING_TOWER COMMAND=SET_PRESSURE_ADVANCE PARAMETER=ADVANCE START=0 FACTOR=0.002 to the G-code, so PA = height x 0.002.
// Read the corners under a lamp: the height where they are sharpest, with no bulge and no gap, gives the value.
cube([50, 50, 32]);

# Precision Parking

A driving-school parking simulator by **Obi Promise**, built for CSC 131.

**Play it:** https://obipromise.github.io/precision-parking/ (3D, works on phones)
**Python edition:** https://obipromise.github.io/precision-parking/python-edition/ (top-down, Pygame)

## What it is

Park a car in 8 lessons that get harder: a forward bay, a tight bay between cars, a 45° angled row, reversing into a bay, parallel parking, a home garage, a parking deck with concrete pillars, and a tight parallel park at dusk.

- **Career:** unlock lessons and earn up to 3 stars each
- **Vs Computer:** race a blue ghost car on Easy, Medium or Hard
- **2 Players:** pass the phone and compare scores over 3 or 5 rounds

Scoring: 300 for parking, up to 500 for accuracy (angle and centering), up to 400 for speed. Each collision costs 100 and each cone 40.

## Controls

| | Phone | Keyboard |
|---|---|---|
| Steer | Drag the steering wheel | ← → |
| Drive / reverse | GAS pedal + P / R / D | ↑ / ↓ (the gear changes automatically when stopped) |
| Brake | BRAKE pedal | Space |
| Park | Tap P | P |

## How it works

| File | What it is |
|---|---|
| `precision-parking.html` | 3D edition source (JavaScript + Three.js) |
| `index.html` | Standalone build of the 3D edition (rebuild with `sh build.sh`) |
| `python/main.py` | Python edition source (pygame-ce) |
| `python-edition/` | Python edition packaged for the web with pygbag |

Main techniques used:
- **Kinematic bicycle model:** the car pivots around its rear axle, like a real car.
- **Separating Axis Theorem:** collision checks between rotated boxes.
- **Catmull-Rom splines:** smooth paths for the computer driver.
- **Parking sensors:** distance checks from points on the front and rear bumpers.
- **Guide lines:** the bumper corners' future path for the current steering angle.

## Run the Python edition locally

```
pip install pygame-ce
python python/main.py
```

To rebuild the web version: `pip install pygbag`, then `python -m pygbag --build python`, then copy `python/build/web/*` into `python-edition/`.

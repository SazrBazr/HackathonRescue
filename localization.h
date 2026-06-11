#ifndef LOCALIZATION_H
#define LOCALIZATION_H

// ============================================================================
//  Localization Engine  —  the "real job" for the C++ ingestion node.
//
//  Given a set of ANCHORS (things with known positions: 4 ground beacons +
//  2 drones) and the measured DISTANCE from each anchor to a victim's
//  bracelet, recover the victim's (x, y, z) position.
//
//  This is multilateration solved by linear least squares. No external
//  libraries — the heavy part is just inverting one 3x3 matrix by hand.
//
//  Z convention: ground = 0, underground = negative, drones = positive.
// ============================================================================

#include <vector>
#include <cmath>
#include <cstdio>

struct Anchor {
    double x, y, z;   // known position of the beacon/drone
    double range;     // measured (and ideally denoised) distance to the victim
    bool   is_master; // exactly one anchor is the (0,0,0) reference
};

struct Position {
    double x, y, z;
    bool   solved;    // false if the geometry was degenerate (matrix singular)
};

// ---- Solve a 3x3 linear system  M * p = v  via the analytic inverse ----
static inline bool solve3x3(const double M[3][3], const double v[3], double out[3]) {
    double det =
        M[0][0]*(M[1][1]*M[2][2] - M[1][2]*M[2][1]) -
        M[0][1]*(M[1][0]*M[2][2] - M[1][2]*M[2][0]) +
        M[0][2]*(M[1][0]*M[2][1] - M[1][1]*M[2][0]);

    if (std::fabs(det) < 1e-9) return false;  // degenerate (e.g. anchors coplanar)

    double inv[3][3];
    inv[0][0] =  (M[1][1]*M[2][2] - M[1][2]*M[2][1]) / det;
    inv[0][1] = -(M[0][1]*M[2][2] - M[0][2]*M[2][1]) / det;
    inv[0][2] =  (M[0][1]*M[1][2] - M[0][2]*M[1][1]) / det;
    inv[1][0] = -(M[1][0]*M[2][2] - M[1][2]*M[2][0]) / det;
    inv[1][1] =  (M[0][0]*M[2][2] - M[0][2]*M[2][0]) / det;
    inv[1][2] = -(M[0][0]*M[1][2] - M[0][2]*M[1][0]) / det;
    inv[2][0] =  (M[1][0]*M[2][1] - M[1][1]*M[2][0]) / det;
    inv[2][1] = -(M[0][0]*M[2][1] - M[0][1]*M[2][0]) / det;
    inv[2][2] =  (M[0][0]*M[1][1] - M[0][1]*M[1][0]) / det;

    for (int i = 0; i < 3; i++)
        out[i] = inv[i][0]*v[0] + inv[i][1]*v[1] + inv[i][2]*v[2];
    return true;
}

// ---- Main entry point: anchors in, victim position out ----
//
//  For each non-master anchor i, subtracting the master's sphere equation
//  cancels the quadratic terms and leaves a linear equation:
//      2*xi*x + 2*yi*y + 2*zi*z = (xi^2+yi^2+zi^2) + r0^2 - ri^2
//
//  Stacking those equations gives A*p = b (usually more rows than unknowns),
//  which we solve in the least-squares sense via the normal equations
//  (A^T A) p = A^T b  — and A^T A is a tidy 3x3.
static inline Position localize(const std::vector<Anchor>& anchors) {
    Position result{0, 0, 0, false};

    // Find the master (the (0,0,0) reference) and its measured range.
    const Anchor* master = nullptr;
    for (const auto& a : anchors) {
        if (a.is_master) { master = &a; break; }
    }
    if (!master || anchors.size() < 4) return result;  // need master + >=3 others
    double r0 = master->range;

    // Accumulate the normal equations directly (no need to store full A, b).
    double AtA[3][3] = {{0,0,0},{0,0,0},{0,0,0}};
    double Atb[3]    = {0,0,0};

    for (const auto& a : anchors) {
        if (a.is_master) continue;
        double row[3] = { 2.0*a.x, 2.0*a.y, 2.0*a.z };
        double bi = (a.x*a.x + a.y*a.y + a.z*a.z) + r0*r0 - a.range*a.range;

        for (int i = 0; i < 3; i++) {
            for (int j = 0; j < 3; j++) AtA[i][j] += row[i]*row[j];
            Atb[i] += row[i]*bi;
        }
    }

    double p[3];
    if (!solve3x3(AtA, Atb, p)) return result;  // geometry too flat -> unsolved

    result = { p[0], p[1], p[2], true };
    return result;
}

#endif // LOCALIZATION_H

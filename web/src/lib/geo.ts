// Device location → nearest city, entirely in the browser: the coordinates never leave the device;
// only the city name is saved (as a food preference). Indian cities, since regional cooking differs
// most there; anywhere else falls back to the time zone's country.

const CITIES: [string, string, number, number][] = [
  ['Bengaluru', 'Karnataka', 12.97, 77.59],
  ['Mysuru', 'Karnataka', 12.3, 76.64],
  ['Mangaluru', 'Karnataka', 12.91, 74.86],
  ['Hubballi', 'Karnataka', 15.36, 75.12],
  ['Chennai', 'Tamil Nadu', 13.08, 80.27],
  ['Coimbatore', 'Tamil Nadu', 11.02, 76.96],
  ['Madurai', 'Tamil Nadu', 9.93, 78.12],
  ['Tiruchirappalli', 'Tamil Nadu', 10.79, 78.7],
  ['Hyderabad', 'Telangana', 17.39, 78.49],
  ['Warangal', 'Telangana', 17.97, 79.59],
  ['Visakhapatnam', 'Andhra Pradesh', 17.69, 83.22],
  ['Vijayawada', 'Andhra Pradesh', 16.51, 80.65],
  ['Tirupati', 'Andhra Pradesh', 13.63, 79.42],
  ['Kochi', 'Kerala', 9.93, 76.27],
  ['Thiruvananthapuram', 'Kerala', 8.52, 76.94],
  ['Kozhikode', 'Kerala', 11.26, 75.78],
  ['Mumbai', 'Maharashtra', 19.08, 72.88],
  ['Pune', 'Maharashtra', 18.52, 73.86],
  ['Nagpur', 'Maharashtra', 21.15, 79.09],
  ['Nashik', 'Maharashtra', 20.0, 73.79],
  ['Panaji', 'Goa', 15.5, 73.83],
  ['Ahmedabad', 'Gujarat', 23.02, 72.57],
  ['Surat', 'Gujarat', 21.17, 72.83],
  ['Vadodara', 'Gujarat', 22.31, 73.18],
  ['Rajkot', 'Gujarat', 22.3, 70.8],
  ['Delhi', 'Delhi', 28.61, 77.21],
  ['Gurugram', 'Haryana', 28.46, 77.03],
  ['Noida', 'Uttar Pradesh', 28.54, 77.39],
  ['Chandigarh', 'Punjab', 30.73, 76.78],
  ['Amritsar', 'Punjab', 31.63, 74.87],
  ['Ludhiana', 'Punjab', 30.9, 75.86],
  ['Jaipur', 'Rajasthan', 26.91, 75.79],
  ['Jodhpur', 'Rajasthan', 26.24, 73.02],
  ['Udaipur', 'Rajasthan', 24.59, 73.71],
  ['Lucknow', 'Uttar Pradesh', 26.85, 80.95],
  ['Kanpur', 'Uttar Pradesh', 26.45, 80.33],
  ['Varanasi', 'Uttar Pradesh', 25.32, 82.97],
  ['Agra', 'Uttar Pradesh', 27.18, 78.01],
  ['Dehradun', 'Uttarakhand', 30.32, 78.03],
  ['Shimla', 'Himachal Pradesh', 31.1, 77.17],
  ['Srinagar', 'Jammu and Kashmir', 34.08, 74.8],
  ['Jammu', 'Jammu and Kashmir', 32.73, 74.86],
  ['Bhopal', 'Madhya Pradesh', 23.26, 77.41],
  ['Indore', 'Madhya Pradesh', 22.72, 75.86],
  ['Raipur', 'Chhattisgarh', 21.25, 81.63],
  ['Patna', 'Bihar', 25.59, 85.14],
  ['Ranchi', 'Jharkhand', 23.34, 85.31],
  ['Kolkata', 'West Bengal', 22.57, 88.36],
  ['Siliguri', 'West Bengal', 26.73, 88.4],
  ['Bhubaneswar', 'Odisha', 20.3, 85.82],
  ['Guwahati', 'Assam', 26.14, 91.74],
  ['Shillong', 'Meghalaya', 25.58, 91.89],
  ['Imphal', 'Manipur', 24.82, 93.94],
  ['Gangtok', 'Sikkim', 27.33, 88.61],
];

/** "Bengaluru, Karnataka, India" for every known city, for the city picker. */
export const CITY_NAMES = CITIES.map(([city, state]) => `${city}, ${state}, India`);

const MAX_KM = 150;

function distanceKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = Math.PI / 180;
  const a =
    Math.sin(((lat2 - lat1) * rad) / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(((lon2 - lon1) * rad) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(a));
}

/** "Bengaluru, Karnataka, India" for a point near a known city; null elsewhere. */
export function nearestCity(lat: number, lon: number): string | null {
  let best: { name: string; km: number } | null = null;
  for (const [city, state, clat, clon] of CITIES) {
    const km = distanceKm(lat, lon, clat, clon);
    if (!best || km < best.km) best = { name: `${city}, ${state}, India`, km };
  }
  return best && best.km <= MAX_KM ? best.name : null;
}

/** Ask the device where it is (the browser shows its own permission prompt). */
export function deviceCity(): Promise<string | null> {
  return new Promise((resolve) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve(nearestCity(pos.coords.latitude, pos.coords.longitude)),
      () => resolve(null),
      { enableHighAccuracy: false, timeout: 15_000, maximumAge: 24 * 3600 * 1000 },
    );
  });
}

# FPV Drone Tracking Backend System
# Complete backend implementation with WebSocket, MAVLink, and database support

import asyncio
import json
import time
import math
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import threading
from queue import Queue, Empty

# WebSocket and HTTP server
import websockets
from aiohttp import web, WSMsgType
import aiohttp_cors

# MAVLink protocol support
try:
    from pymavlink import mavutil
    MAVLINK_AVAILABLE = True
except ImportError:
    print("PyMAVLink not available. Install with: pip install pymavlink")
    MAVLINK_AVAILABLE = False

# GPS and geographic calculations
try:
    import geopy.distance
    from geopy import Point
    GEOPY_AVAILABLE = True
except ImportError:
    print("Geopy not available. Install with: pip install geopy")
    GEOPY_AVAILABLE = False

# Load configuration from file or use defaults
def load_config():
    """Load configuration from config.json or use defaults"""
    default_config = {
        'server': {
            'host': '0.0.0.0',
            'port': 8080,
            'websocket_port': 8081
        },
        'drone': {
            'connection_string': 'udp:127.0.0.1:14550',  # Default SITL connection
            'heartbeat_timeout': 10,
            'telemetry_rate': 10  # Hz
        },
        'database': {
            'path': 'drone_data.db'
        },
        'logging': {
            'level': 'INFO',
            'file': 'drone_backend.log'
        },
        'simulation': {
            'enabled': True,
            'center_lat': 40.7589,
            'center_lon': -73.9851,
            'flight_radius': 0.001,
            'max_altitude': 100
        }
    }
    
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
            # Merge with defaults
            for key in default_config:
                if key not in config:
                    config[key] = default_config[key]
                elif isinstance(default_config[key], dict):
                    for subkey in default_config[key]:
                        if subkey not in config[key]:
                            config[key][subkey] = default_config[key][subkey]
            return config
    except FileNotFoundError:
        print("config.json not found, using default configuration")
        return default_config
    except json.JSONDecodeError:
        print("Invalid config.json, using default configuration")
        return default_config

CONFIG = load_config()

# Setup logging
logging.basicConfig(
    level=getattr(logging, CONFIG['logging']['level']),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(CONFIG['logging']['file']),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Data models
class FlightMode(Enum):
    STABILIZE = "STABILIZE"
    ACRO = "ACRO"
    ALT_HOLD = "ALT_HOLD"
    AUTO = "AUTO"
    GUIDED = "GUIDED"
    LOITER = "LOITER"
    LAND = "LAND"
    RTL = "RTL"
    SPORT = "SPORT"

class DroneStatus(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    ARMED = "ARMED"
    FLYING = "FLYING"
    LANDING = "LANDING"
    ERROR = "ERROR"

@dataclass
class GPSData:
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    fix_type: int = 0
    satellites_visible: int = 0
    hdop: float = 0.0
    vdop: float = 0.0

@dataclass
class AttitudeData:
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    rollspeed: float = 0.0
    pitchspeed: float = 0.0
    yawspeed: float = 0.0

@dataclass
class BatteryData:
    voltage: float = 0.0
    current: float = 0.0
    remaining: int = 100
    temperature: float = 0.0

@dataclass
class TelemetryData:
    timestamp: float
    gps: GPSData
    attitude: AttitudeData
    battery: BatteryData
    flight_mode: str
    armed: bool
    groundspeed: float = 0.0
    airspeed: float = 0.0
    climb_rate: float = 0.0
    heading: float = 0.0
    home_distance: float = 0.0
    rssi: int = 0

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Flight sessions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS flight_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                duration INTEGER,
                max_altitude REAL,
                max_distance REAL,
                max_speed REAL,
                total_distance REAL,
                battery_consumed INTEGER
            )
        ''')
        
        # Telemetry data table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                timestamp REAL,
                latitude REAL,
                longitude REAL,
                altitude REAL,
                roll REAL,
                pitch REAL,
                yaw REAL,
                groundspeed REAL,
                battery_voltage REAL,
                battery_remaining INTEGER,
                flight_mode TEXT,
                armed BOOLEAN,
                FOREIGN KEY (session_id) REFERENCES flight_sessions (id)
            )
        ''')
        
        # Waypoints table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS waypoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                latitude REAL,
                longitude REAL,
                altitude REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Flight logs table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS flight_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                timestamp REAL,
                level TEXT,
                message TEXT,
                FOREIGN KEY (session_id) REFERENCES flight_sessions (id)
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")
    
    def start_flight_session(self) -> int:
        """Start a new flight session"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO flight_sessions (start_time) VALUES (?)
        ''', (datetime.now(),))
        session_id = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"Started flight session {session_id}")
        return session_id
    
    def end_flight_session(self, session_id: int, stats: Dict):
        """End a flight session with statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE flight_sessions SET 
                end_time = ?, duration = ?, max_altitude = ?, 
                max_distance = ?, max_speed = ?, total_distance = ?,
                battery_consumed = ?
            WHERE id = ?
        ''', (
            datetime.now(), stats.get('duration', 0), stats.get('max_altitude', 0),
            stats.get('max_distance', 0), stats.get('max_speed', 0),
            stats.get('total_distance', 0), stats.get('battery_consumed', 0),
            session_id
        ))
        conn.commit()
        conn.close()
        logger.info(f"Ended flight session {session_id}")
    
    def save_telemetry(self, session_id: int, data: TelemetryData):
        """Save telemetry data to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO telemetry (
                session_id, timestamp, latitude, longitude, altitude,
                roll, pitch, yaw, groundspeed, battery_voltage,
                battery_remaining, flight_mode, armed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id, data.timestamp, data.gps.latitude, data.gps.longitude,
            data.gps.altitude, data.attitude.roll, data.attitude.pitch,
            data.attitude.yaw, data.groundspeed, data.battery.voltage,
            data.battery.remaining, data.flight_mode, data.armed
        ))
        conn.commit()
        conn.close()
    
    def log_message(self, session_id: int, level: str, message: str):
        """Log a flight message"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO flight_logs (session_id, timestamp, level, message)
            VALUES (?, ?, ?, ?)
        ''', (session_id, time.time(), level, message))
        conn.commit()
        conn.close()

    def get_flight_history(self, limit: int = 50):
        """Get flight history"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, start_time, end_time, duration,
                   max_altitude, max_distance, max_speed, 
                   total_distance, battery_consumed
            FROM flight_sessions 
            ORDER BY start_time DESC 
            LIMIT ?
        ''', (limit,))
        
        sessions = []
        for row in cursor.fetchall():
            sessions.append({
                'id': row[0],
                'start_time': row[1],
                'end_time': row[2],
                'duration': row[3],
                'max_altitude': row[4],
                'max_distance': row[5],
                'max_speed': row[6],
                'total_distance': row[7],
                'battery_consumed': row[8]
            })
        
        conn.close()
        return sessions

class DroneController:
    def __init__(self):
        self.db = DatabaseManager(CONFIG['database']['path'])
        self.status = DroneStatus.DISCONNECTED
        self.current_session_id = None
        self.flight_stats = {
            'start_time': None,
            'max_altitude': 0,
            'max_distance': 0,
            'max_speed': 0,
            'total_distance': 0,
            'battery_start': 100,
            'last_position': None
        }
        
        self.current_telemetry = TelemetryData(
            timestamp=time.time(),
            gps=GPSData(),
            attitude=AttitudeData(),
            battery=BatteryData(),
            flight_mode="STABILIZE",
            armed=False
        )
        
        # WebSocket clients
        self.websocket_clients = set()
        
    async def start(self):
        """Start the drone controller"""
        logger.info("Starting drone controller...")
        
        # Start telemetry simulation
        asyncio.create_task(self._simulate_telemetry())
    
    async def _simulate_telemetry(self):
        """Simulate telemetry data when no real drone is connected"""
        logger.info("Starting telemetry simulation")
        start_time = time.time()
        
        while True:
            try:
                current_time = time.time()
                elapsed = current_time - start_time
                
                # Simulate GPS movement in a circle
                center_lat = 40.7589
                center_lon = -73.9851
                radius = 0.001  # About 100 meters
                
                self.current_telemetry.gps.latitude = center_lat + radius * math.sin(elapsed * 0.1)
                self.current_telemetry.gps.longitude = center_lon + radius * math.cos(elapsed * 0.1)
                self.current_telemetry.gps.altitude = 50 + 20 * math.sin(elapsed * 0.05)
                self.current_telemetry.gps.fix_type = 3
                self.current_telemetry.gps.satellites_visible = 12
                
                # Simulate attitude
                self.current_telemetry.attitude.roll = 5 * math.sin(elapsed * 0.2)
                self.current_telemetry.attitude.pitch = 3 * math.cos(elapsed * 0.15)
                self.current_telemetry.attitude.yaw = (elapsed * 10) % 360
                
                # Simulate battery drain
                self.current_telemetry.battery.voltage = 16.8 - (elapsed * 0.001)
                self.current_telemetry.battery.remaining = max(0, 100 - int(elapsed * 0.1))
                
                # Simulate speed and heading
                self.current_telemetry.groundspeed = 15 + 5 * math.sin(elapsed * 0.3)
                self.current_telemetry.heading = (elapsed * 20) % 360
                
                self.current_telemetry.timestamp = current_time
                
                # Broadcast to clients
                await self._broadcast_telemetry()
                
                await asyncio.sleep(0.1)  # 10Hz updates
                
            except Exception as e:
                logger.error(f"Error in telemetry simulation: {e}")
                await asyncio.sleep(1)
    
    async def _broadcast_telemetry(self):
        """Broadcast telemetry to all WebSocket clients"""
        if not self.websocket_clients:
            return
        
        telemetry_dict = asdict(self.current_telemetry)
        message = {
            'type': 'telemetry',
            'data': telemetry_dict,
            'status': self.status.value
        }
        
        # Remove disconnected clients
        disconnected_clients = set()
        
        for client in self.websocket_clients:
            try:
                await client.send(json.dumps(message))
            except:
                disconnected_clients.add(client)
        
        self.websocket_clients -= disconnected_clients
    
    async def handle_command(self, command: str, params: Dict = None):
        """Handle command from client"""
        logger.info(f"Received command: {command} with params: {params}")
        
        try:
            if command == 'connect':
                self.status = DroneStatus.CONNECTED
                return {'success': True, 'message': 'Connected to drone (simulation)'}
            
            elif command == 'disconnect':
                self.status = DroneStatus.DISCONNECTED
                return {'success': True, 'message': 'Disconnected from drone'}
            
            elif command == 'arm':
                self.current_telemetry.armed = True
                self.status = DroneStatus.ARMED
                return {'success': True, 'message': 'Drone armed'}
            
            elif command == 'disarm':
                self.current_telemetry.armed = False
                self.status = DroneStatus.CONNECTED
                return {'success': True, 'message': 'Drone disarmed'}
            
            elif command == 'rtl':
                self.current_telemetry.flight_mode = 'RTL'
                return {'success': True, 'message': 'Return to launch activated'}
            
            elif command == 'land':
                self.current_telemetry.flight_mode = 'LAND'
                return {'success': True, 'message': 'Landing mode activated'}
            
            elif command == 'set_mode':
                mode = params.get('mode', 'STABILIZE') if params else 'STABILIZE'
                self.current_telemetry.flight_mode = mode
                return {'success': True, 'message': f'Flight mode set to {mode}'}
            
            elif command == 'start_session':
                self.current_session_id = self.db.start_flight_session()
                self.flight_stats['start_time'] = time.time()
                self.flight_stats['battery_start'] = self.current_telemetry.battery.remaining
                return {'success': True, 'session_id': self.current_session_id}
            
            elif command == 'end_session':
                if self.current_session_id:
                    duration = time.time() - self.flight_stats['start_time']
                    battery_consumed = self.flight_stats['battery_start'] - self.current_telemetry.battery.remaining
                    
                    stats = {
                        'duration': duration,
                        'max_altitude': self.flight_stats['max_altitude'],
                        'max_distance': self.flight_stats['max_distance'],
                        'max_speed': self.flight_stats['max_speed'],
                        'total_distance': self.flight_stats['total_distance'],
                        'battery_consumed': battery_consumed
                    }
                    
                    self.db.end_flight_session(self.current_session_id, stats)
                    self.current_session_id = None
                    return {'success': True, 'stats': stats}
                else:
                    return {'success': False, 'message': 'No active session'}
            
            else:
                return {'success': False, 'message': f'Unknown command: {command}'}
                
        except Exception as e:
            logger.error(f"Error handling command {command}: {e}")
            return {'success': False, 'message': str(e)}

# WebSocket handler
async def websocket_handler(websocket, path, drone_controller):
    """Handle WebSocket connections"""
    logger.info(f"New WebSocket connection from {websocket.remote_address}")
    drone_controller.websocket_clients.add(websocket)
    
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                command = data.get('command')
                params = data.get('params', {})
                
                response = await drone_controller.handle_command(command, params)
                await websocket.send(json.dumps(response))
                
            except json.JSONDecodeError:
                await websocket.send(json.dumps({
                    'success': False,
                    'message': 'Invalid JSON'
                }))
            except Exception as e:
                logger.error(f"Error processing WebSocket message: {e}")
                await websocket.send(json.dumps({
                    'success': False,
                    'message': str(e)
                }))
                
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"WebSocket connection closed: {websocket.remote_address}")
    finally:
        drone_controller.websocket_clients.discard(websocket)

# HTTP API handlers
async def get_telemetry(request):
    """Get current telemetry data"""
    drone_controller = request.app['drone_controller']
    telemetry_dict = asdict(drone_controller.current_telemetry)
    
    return web.json_response({
        'telemetry': telemetry_dict,
        'status': drone_controller.status.value,
        'session_id': drone_controller.current_session_id
    })

async def post_command(request):
    """Handle command via HTTP POST"""
    drone_controller = request.app['drone_controller']
    
    try:
        data = await request.json()
        command = data.get('command')
        params = data.get('params', {})
        
        response = await drone_controller.handle_command(command, params)
        return web.json_response(response)
        
    except Exception as e:
        return web.json_response({
            'success': False,
            'message': str(e)
        }, status=400)

async def get_flight_history(request):
    """Get flight history from database"""
    drone_controller = request.app['drone_controller']
    sessions = drone_controller.db.get_flight_history()
    return web.json_response({'flights': sessions})

async def get_health(request):
    """Health check endpoint"""
    return web.json_response({
        'status': 'healthy',
        'timestamp': time.time()
    })

async def serve_static(request):
    """Serve the HTML frontend"""
    return web.FileResponse('fpv_drone_tracker.html')

def create_app():
    """Create and configure the web application"""
    app = web.Application()
    
    # Create drone controller
    drone_controller = DroneController()
    app['drone_controller'] = drone_controller
    
    # Setup CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*"
        )
    })
    
    # Add routes
    app.router.add_get('/', serve_static)
    app.router.add_get('/api/telemetry', get_telemetry)
    app.router.add_post('/api/command', post_command)
    app.router.add_get('/api/flights', get_flight_history)
    app.router.add_get('/api/health', get_health)
    
    # Add CORS to all routes
    for route in list(app.router.routes()):
        cors.add(route)
    
    return app

async def main():
    """Main application entry point"""
    logger.info("Starting FPV Drone Tracking Backend")
    
    # Create web application
    app = create_app()
    drone_controller = app['drone_controller']
    
    # Start drone controller
    await drone_controller.start()
    
    # Start WebSocket server
    websocket_server = websockets.serve(
        lambda ws, path: websocket_handler(ws, path, drone_controller),
        CONFIG['server']['host'],
        CONFIG['server']['websocket_port']
    )
    
    # Start HTTP server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, CONFIG['server']['host'], CONFIG['server']['port'])
    await site.start()
    
    logger.info(f"HTTP server started on {CONFIG['server']['host']}:{CONFIG['server']['port']}")
    logger.info(f"WebSocket server started on {CONFIG['server']['host']}:{CONFIG['server']['websocket_port']}")
    logger.info("Backend is ready!")
    
    # Start both servers
    await asyncio.gather(
        websocket_server,
        asyncio.Event().wait()  # Run forever
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
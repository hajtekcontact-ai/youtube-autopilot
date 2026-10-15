"""Idea generator module for YouTube AI Autopilot.

Generates high-engagement video concepts using Google Gemini API when configured,
with an extensive fallback engine containing 400+ evergreen topic templates across
8 distinct niches. Implements deduplication against database history, engagement scoring,
and rate-limited API interactions.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Union

logger = logging.getLogger(__name__)

# Try importing project dependencies gracefully
try:
    from src.config import current_config, Config
except ImportError:
    current_config = None
    Config = None

try:
    from src.database import ContentIdea as DBContentIdea, Video as DBVideo, get_db
except ImportError:
    DBContentIdea = None
    DBVideo = None
    get_db = None

try:
    from sqlalchemy import text
except ImportError:
    text = None

try:
    from src.utils.helpers import hash_content, safe_json_loads, generate_unique_id
except ImportError:
    import hashlib

    def hash_content(text_in: str) -> str:
        return hashlib.sha256((text_in or "").strip().lower().encode("utf-8")).hexdigest()

    def safe_json_loads(text_in: Any, default: Any = None) -> Any:
        if not text_in:
            return default
        try:
            return json.loads(text_in)
        except Exception:
            return default

    def generate_unique_id() -> str:
        return str(uuid.uuid4())

# Optional Google Generative AI
try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    genai = None
    HAS_GENAI = False


@dataclass
class ContentIdea:
    """Structured representation of a generated video idea."""

    topic: str
    title: str
    description: str
    niche: str
    id: str = field(default_factory=generate_unique_id)
    source: str = "template"  # 'gemini' or 'template'
    score: float = 7.5
    used: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert idea to a dictionary representation.

        Returns:
            Dict[str, Any]: Key-value data of content idea.
        """
        return {
            "id": self.id,
            "topic": self.topic,
            "title": self.title,
            "description": self.description,
            "niche": self.niche,
            "source": self.source,
            "score": round(self.score, 2),
            "used": self.used,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at),
            "metadata": self.metadata,
        }

    def to_db_model(self) -> Any:
        """Convert to SQLAlchemy DBContentIdea instance if available.

        Returns:
            Optional[DBContentIdea]: ORM model or None.
        """
        if DBContentIdea is None:
            return None
        return DBContentIdea(
            topic=self.topic,
            title=self.title,
            description=self.description,
            niche=self.niche,
            source=self.source,
            used=self.used,
            score=self.score,
            metadata_json=json.dumps(self.metadata),
        )


class RateLimiter:
    """Thread-safe rate limiter enforcing requests-per-minute constraints."""

    def __init__(self, rpm: int = 15):
        """Initialize the rate limiter with target requests per minute.

        Args:
            rpm: Maximum allowed requests per minute.
        """
        self.rpm = max(1, rpm)
        self.min_interval = 60.0 / self.rpm
        self.last_call_time = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block execution until rate limit window permits next request."""
        with self._lock:
            now = time.time()
            elapsed = now - self.last_call_time
            if elapsed < self.min_interval:
                sleep_needed = self.min_interval - elapsed
                time.sleep(sleep_needed)
            self.last_call_time = time.time()


# Built-in evergreen topics across 8 categories (50+ each, 416 total)
EVERGREEN_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "science": [
        {
            "topic": "Quantum Entanglement",
            "title": "Why Einstein Called This 'Spooky Action at a Distance'",
            "description": "How two particles communicate instantaneously across light-years and what it means for physics.",
            "score": 9.3,
            "keywords": [
                "quantum physics",
                "einstein",
                "particles"
            ]
        },
        {
            "topic": "Dark Matter Mystery",
            "title": "The Invisible 85% of the Universe We Cannot Find",
            "description": "Scientists know dark matter exists, but after decades of searching, we still cannot detect a single particle.",
            "score": 9.1,
            "keywords": [
                "dark matter",
                "astrophysics",
                "cosmology"
            ]
        },
        {
            "topic": "Nuclear Fusion Energy",
            "title": "The Artificial Sun: Why Clean Unlimited Energy is Closer Than You Think",
            "description": "How magnetic confinement tokamaks like ITER are recreating the power of stars on Earth.",
            "score": 9.4,
            "keywords": [
                "nuclear fusion",
                "clean energy",
                "physics"
            ]
        },
        {
            "topic": "CRISPR Gene Editing",
            "title": "The Code of Life: How CRISPR is Rewriting Human DNA",
            "description": "How bacterial immune systems became humanity's most powerful genetic engineering tool.",
            "score": 9.2,
            "keywords": [
                "crispr",
                "genetics",
                "biotechnology"
            ]
        },
        {
            "topic": "Black Hole Information Paradox",
            "title": "What Really Happens to Information When It Falls into a Black Hole?",
            "description": "Stephen Hawking's greatest paradox and how it might break the laws of physics.",
            "score": 9.5,
            "keywords": [
                "black hole",
                "hawking radiation",
                "theoretical physics"
            ]
        },
        {
            "topic": "Superconductors",
            "title": "Zero Resistance: The Race to Room-Temperature Superconductors",
            "description": "Why transmitting electricity with zero energy loss would revolutionize global technology.",
            "score": 8.9,
            "keywords": [
                "superconductors",
                "materials science",
                "physics"
            ]
        },
        {
            "topic": "Antimatter",
            "title": "The Most Expensive Substance on Earth ($62 Trillion Per Gram)",
            "description": "What happens when matter meets antimatter, and why didn't the universe destroy itself?",
            "score": 9.6,
            "keywords": [
                "antimatter",
                "cern",
                "particle physics"
            ]
        },
        {
            "topic": "Chaos Theory & Butterfly Effect",
            "title": "The Mathematical Reason Why Weather is Impossible to Predict",
            "description": "How tiny changes in complex nonlinear systems lead to completely unpredictable outcomes.",
            "score": 8.8,
            "keywords": [
                "chaos theory",
                "mathematics",
                "butterfly effect"
            ]
        },
        {
            "topic": "Thermodynamics & Time's Arrow",
            "title": "Why Time Only Moves Forward: The Law of Entropy Explained",
            "description": "The second law of thermodynamics and why you can never unscramble an egg.",
            "score": 9.0,
            "keywords": [
                "entropy",
                "time",
                "thermodynamics"
            ]
        },
        {
            "topic": "General Relativity",
            "title": "Gravity Isn't a Force: Einstein's Bizarre Curved Spacetime",
            "description": "Why massive objects warp the fabric of reality itself and slow down time.",
            "score": 9.2,
            "keywords": [
                "relativity",
                "einstein",
                "spacetime"
            ]
        },
        {
            "topic": "Graphene Wonder Material",
            "title": "One Atom Thick and 200 Times Stronger Than Steel",
            "description": "The discovery and unreal properties of 2D carbon allotropes.",
            "score": 8.7,
            "keywords": [
                "graphene",
                "nanotechnology",
                "materials science"
            ]
        },
        {
            "topic": "The Fermi Paradox",
            "title": "If the Universe is Teeming With Life, Where is Everybody?",
            "description": "Enrico Fermi's famous question and the terrifying Great Filter hypothesis.",
            "score": 9.7,
            "keywords": [
                "fermi paradox",
                "alien life",
                "astrobiology"
            ]
        },
        {
            "topic": "String Theory",
            "title": "Is Our Universe Made of 11-Dimensional Vibrating Strings?",
            "description": "The elusive Theory of Everything that attempts to unify gravity and quantum mechanics.",
            "score": 9.1,
            "keywords": [
                "string theory",
                "quantum gravity",
                "theoretical physics"
            ]
        },
        {
            "topic": "Gravitational Waves",
            "title": "Listening to the Ripples of Colliding Black Holes Across Spacetime",
            "description": "How LIGO detected gravitational waves traveling for over a billion years.",
            "score": 8.9,
            "keywords": [
                "gravitational waves",
                "ligo",
                "astronomy"
            ]
        },
        {
            "topic": "The Higgs Boson",
            "title": "The 'God Particle' That Gives Mass to Everything in the Universe",
            "description": "How the Higgs field creates inertia and why the universe wouldn't exist without it.",
            "score": 9.0,
            "keywords": [
                "higgs boson",
                "cern",
                "standard model"
            ]
        },
        {
            "topic": "Plate Tectonics",
            "title": "How Earth Recycles Its Crust and Prevents Runaway Climate Disaster",
            "description": "The churning mantle engine that drives earthquakes, volcanoes, and continents.",
            "score": 8.6,
            "keywords": [
                "geology",
                "earth science",
                "plate tectonics"
            ]
        },
        {
            "topic": "Hydrothermal Vents",
            "title": "Alien Ecosystems in Total Darkness Powered by Volcanic Chemicals",
            "description": "Life thriving at 400 degrees Celsius without a single ray of sunlight.",
            "score": 8.8,
            "keywords": [
                "deep sea",
                "chemosynthesis",
                "marine biology"
            ]
        },
        {
            "topic": "Quantum Tunneling",
            "title": "How Particles Walk Through Solid Walls in Quantum Physics",
            "description": "The strange phenomenon that powers nuclear fusion in the sun and your smartphone flash memory.",
            "score": 9.1,
            "keywords": [
                "quantum tunneling",
                "quantum mechanics",
                "physics"
            ]
        },
        {
            "topic": "The Casimir Effect",
            "title": "Creating Real Forces Out of the Empty Vacuum of Space",
            "description": "Why empty space is actually boiling with quantum virtual particles.",
            "score": 8.9,
            "keywords": [
                "casimir effect",
                "quantum vacuum",
                "zero point energy"
            ]
        },
        {
            "topic": "Absolute Zero",
            "title": "What Happens at Minus 273.15\u00b0C When Atoms Freeze Completely?",
            "description": "Bose-Einstein condensates and the bizarre behavior of matter near absolute zero.",
            "score": 9.0,
            "keywords": [
                "absolute zero",
                "thermodynamics",
                "cryogenics"
            ]
        },
        {
            "topic": "The Standard Model of Physics",
            "title": "The Blueprint of Everything: The 17 Fundamental Particles",
            "description": "Quarks, leptons, and bosons: the puzzle pieces that construct physical reality.",
            "score": 8.7,
            "keywords": [
                "standard model",
                "particle physics",
                "quarks"
            ]
        },
        {
            "topic": "Quantum Photosynthesis",
            "title": "Plants Use Quantum Mechanics to Harvest Sunlight With 99% Efficiency",
            "description": "How biological evolution solved quantum coherence billions of years before humans.",
            "score": 9.2,
            "keywords": [
                "quantum biology",
                "photosynthesis",
                "biophysics"
            ]
        },
        {
            "topic": "Sonoluminescence",
            "title": "Turning Sound Into Light: Creating Mini Stars with Soundwaves in Liquid",
            "description": "How collapsing acoustic cavitation bubbles heat up hotter than the surface of the sun.",
            "score": 9.3,
            "keywords": [
                "sonoluminescence",
                "physics",
                "acoustics"
            ]
        },
        {
            "topic": "Wave-Particle Duality",
            "title": "The Double-Slit Experiment That Proved Reality Changes When Observed",
            "description": "Why light behaves like a wave until you measure it, turning into particles.",
            "score": 9.5,
            "keywords": [
                "double slit experiment",
                "quantum physics",
                "wave particle duality"
            ]
        },
        {
            "topic": "Turbulence Physics",
            "title": "The Unsolved Million-Dollar Equation of Water Flow",
            "description": "Why Navier-Stokes turbulence remains one of the hardest open problems in modern physics.",
            "score": 8.8,
            "keywords": [
                "fluid dynamics",
                "turbulence",
                "mathematics"
            ]
        },
        {
            "topic": "Synthetic Biology",
            "title": "Building Living Organisms From Scratch in a Test Tube",
            "description": "Creating artificial genomes and custom bacteria that produce medicine and fuel.",
            "score": 8.9,
            "keywords": [
                "synthetic biology",
                "genetics",
                "bioengineering"
            ]
        },
        {
            "topic": "Nanotechnology",
            "title": "Microscopic Machines Operating at the Scale of Individual Molecules",
            "description": "How nano-robots could soon travel through our bloodstream to destroy cancer cells.",
            "score": 9.1,
            "keywords": [
                "nanotechnology",
                "medicine",
                "future tech"
            ]
        },
        {
            "topic": "Cosmic Rays",
            "title": "Ghost Particles Bombarding Earth From Dying Stars",
            "description": "Ultra-high-energy cosmic rays traveling at 99.999% the speed of light.",
            "score": 8.7,
            "keywords": [
                "cosmic rays",
                "astrophysics",
                "radiation"
            ]
        },
        {
            "topic": "Dark Energy",
            "title": "The Mysterious Repulsive Force Tearing the Universe Apart",
            "description": "Why cosmic expansion is accelerating and what it means for the ultimate fate of reality.",
            "score": 9.4,
            "keywords": [
                "dark energy",
                "cosmic expansion",
                "cosmology"
            ]
        },
        {
            "topic": "Neutron Stars",
            "title": "A Sugar Cube Weighing 1 Billion Tons: The Densest Matter in Existence",
            "description": "What happens when a giant star collapses into a ball of pure atomic nuclei.",
            "score": 9.6,
            "keywords": [
                "neutron star",
                "astrophysics",
                "supernova"
            ]
        },
        {
            "topic": "Carbon Capture Tech",
            "title": "Can Giant Machines Vacuum Carbon Dioxide Directly From the Sky?",
            "description": "The chemical engineering fighting climate change through direct air capture.",
            "score": 8.8,
            "keywords": [
                "carbon capture",
                "climate tech",
                "engineering"
            ]
        },
        {
            "topic": "Brain-Computer Interfaces",
            "title": "Translating Thoughts Into Digital Signals with Neural Implants",
            "description": "How paralyzed patients are typing and controlling robotic limbs with their minds.",
            "score": 9.3,
            "keywords": [
                "bci",
                "neuroscience",
                "bioengineering"
            ]
        },
        {
            "topic": "Magnetars",
            "title": "The Strongest Magnets in the Universe That Can Dissolve Atoms",
            "description": "Magnetic fields a trillion times stronger than Earth's that distort atomic electron clouds.",
            "score": 9.5,
            "keywords": [
                "magnetar",
                "space",
                "astrophysics"
            ]
        },
        {
            "topic": "Metamaterials",
            "title": "Invisibility Cloaks: Bending Light Around Objects Using Metasurfaces",
            "description": "How engineered materials with negative refraction index hide objects from radar and light.",
            "score": 9.0,
            "keywords": [
                "metamaterials",
                "optics",
                "physics"
            ]
        },
        {
            "topic": "Quantum Teleportation",
            "title": "Teleporting Quantum Data Across Hundreds of Kilometers",
            "description": "Why quantum entanglement allows data transfer without traveling through physical space.",
            "score": 9.2,
            "keywords": [
                "quantum teleportation",
                "quantum computing",
                "physics"
            ]
        },
        {
            "topic": "Semiconductor Physics",
            "title": "How We Etch 50 Billion Transistors Onto a Fingernail-Sized Chip",
            "description": "Extreme ultraviolet lithography and the quantum limits of silicon chips.",
            "score": 9.1,
            "keywords": [
                "semiconductors",
                "euv lithography",
                "microchips"
            ]
        },
        {
            "topic": "Epigenetics",
            "title": "How Your Daily Choices Physically Alter Your DNA Expression",
            "description": "Why your lifestyle switches genes on and off without changing the underlying genetic code.",
            "score": 9.0,
            "keywords": [
                "epigenetics",
                "genetics",
                "biology"
            ]
        },
        {
            "topic": "Radio Astronomy",
            "title": "How Giant Radio Dishes Map the Invisible Universe Across Billions of Light-Years",
            "description": "Listening to ancient cosmic radio waves from the cosmic dawn.",
            "score": 8.7,
            "keywords": [
                "radio astronomy",
                "telescopes",
                "space"
            ]
        },
        {
            "topic": "Large Hadron Collider",
            "title": "Smashing Particles at 99.999999% Light Speed Inside a 27km Tunnel",
            "description": "Recreating conditions mere picoseconds after the Big Bang under the Swiss-French border.",
            "score": 9.4,
            "keywords": [
                "cern",
                "lhc",
                "particle physics"
            ]
        },
        {
            "topic": "Cellular Automata",
            "title": "How 4 Simple Rules Create Mind-Blowing Infinite Complexity",
            "description": "John Conway's Game of Life and how complex life might emerge from simple mathematics.",
            "score": 8.9,
            "keywords": [
                "cellular automata",
                "game of life",
                "computer science"
            ]
        },
        {
            "topic": "Small Modular Reactors",
            "title": "The Next Generation of Walk-Away Safe Mini Nuclear Power Plants",
            "description": "How factory-built micro-reactors could power remote cities and data centers safely.",
            "score": 8.8,
            "keywords": [
                "nuclear energy",
                "smr",
                "engineering"
            ]
        },
        {
            "topic": "Bioluminescence Chemistry",
            "title": "The Chemical Reaction Behind Glowing Animals in the Deep Sea",
            "description": "How luciferin and luciferase transform biochemical energy into glowing cold light.",
            "score": 8.6,
            "keywords": [
                "bioluminescence",
                "chemistry",
                "marine biology"
            ]
        },
        {
            "topic": "Quantum Dots",
            "title": "Artificial Atoms Emitting Pure Colors in Displays and Solar Cells",
            "description": "The 2023 Nobel Prize chemistry discovery revolutionizing screens and medical imaging.",
            "score": 8.7,
            "keywords": [
                "quantum dots",
                "nanotechnology",
                "chemistry"
            ]
        },
        {
            "topic": "Plasma Physics",
            "title": "The Fourth State of Matter That Makes Up 99% of the Visible Universe",
            "description": "Ionized gases, solar winds, and why controlling plasma is the key to star-power on Earth.",
            "score": 8.8,
            "keywords": [
                "plasma physics",
                "physics",
                "matter"
            ]
        },
        {
            "topic": "Solar Flares & CME",
            "title": "How One Huge Solar Storm Could Knock Out Earth's Power Grids in Minutes",
            "description": "Coronal mass ejections and the catastrophic threat to modern digital infrastructure.",
            "score": 9.3,
            "keywords": [
                "solar flares",
                "space weather",
                "sun"
            ]
        },
        {
            "topic": "Cryonics Science",
            "title": "Can Humans Really Be Frozen in Liquid Nitrogen and Revived Later?",
            "description": "The biophysics of vitrification and the massive scientific hurdles of cryopreservation.",
            "score": 8.9,
            "keywords": [
                "cryonics",
                "biology",
                "future medicine"
            ]
        },
        {
            "topic": "Optical Cloaking",
            "title": "The Real Science of Bending Light Rays to Make Objects Disappear",
            "description": "Transformation optics and how refractive lenses fool the human eye in broad daylight.",
            "score": 8.8,
            "keywords": [
                "invisibility",
                "optics",
                "physics"
            ]
        },
        {
            "topic": "Geophysics of Earth's Core",
            "title": "The Solid Iron Core Spinning Faster Than Earth Itself",
            "description": "The geodynamo generating Earth's protective magnetic field 3,000 miles beneath our feet.",
            "score": 9.0,
            "keywords": [
                "earth core",
                "geophysics",
                "magnetic field"
            ]
        },
        {
            "topic": "Muon Tomography",
            "title": "Using Cosmic Ray Particles to See Inside Ancient Pyramids and Volcanoes",
            "description": "How high-energy subatomic muons revealed hidden chambers in the Great Pyramid of Giza.",
            "score": 9.2,
            "keywords": [
                "muons",
                "particle physics",
                "archaeology"
            ]
        },
        {
            "topic": "Tachyon Physics & Faster-Than-Light",
            "title": "Why the Cosmic Speed Limit of Light Cannot Be Broken",
            "description": "Einstein's causality paradox and why moving faster than light means traveling backward in time.",
            "score": 9.4,
            "keywords": [
                "speed of light",
                "special relativity",
                "physics"
            ]
        },
        {
            "topic": "Bose-Einstein Condensate",
            "title": "When Millions of Atoms Merge Into a Single Giant Quantum Super-Atom",
            "description": "The fifth state of matter where quantum mechanics becomes visible to the human eye.",
            "score": 9.1,
            "keywords": [
                "bose einstein",
                "quantum physics",
                "states of matter"
            ]
        },
        {
            "topic": "Quantum Computing Supremacy",
            "title": "How Qubits Solve in Seconds What Supercomputers Need 10,000 Years For",
            "description": "Superposition and interference calculations redefining cryptography and chemistry.",
            "score": 9.5,
            "keywords": [
                "quantum computing",
                "qubits",
                "computer science"
            ]
        }
    ],
    "technology": [
        {
            "topic": "Large Language Models",
            "title": "How Generative AI Actually Predicts the Next Word",
            "description": "Inside the transformer architecture that powers ChatGPT and generative AI.",
            "score": 9.3,
            "keywords": [
                "ai",
                "llm",
                "machine learning"
            ]
        },
        {
            "topic": "Humanoid Robotics",
            "title": "Why Every Tech Giant is Racing to Build Autonomous Humanoid Robots",
            "description": "The mechanics of bipedal balance, visual perception, and dexterous hands.",
            "score": 9.2,
            "keywords": [
                "robotics",
                "automation",
                "humanoids"
            ]
        },
        {
            "topic": "Solid-State Batteries",
            "title": "The Battery Breakthrough That Will Give EVs 1,000-Mile Range",
            "description": "Replacing liquid electrolytes with solid ceramics to prevent fires and double density.",
            "score": 9.4,
            "keywords": [
                "solid state battery",
                "electric vehicles",
                "energy tech"
            ]
        },
        {
            "topic": "Edge AI Inference",
            "title": "Running Trillion-Parameter AI Models on Your Pocket Phone",
            "description": "Model quantization, neural processing units (NPUs), and on-device machine intelligence.",
            "score": 8.9,
            "keywords": [
                "edge ai",
                "npu",
                "chips"
            ]
        },
        {
            "topic": "Autonomous Self-Driving Vehicles",
            "title": "The Sensor Fusion War: LiDAR vs Pure Camera Vision",
            "description": "How self-driving algorithms make millisecond life-or-death decisions on highways.",
            "score": 9.1,
            "keywords": [
                "autonomous vehicles",
                "tesla vision",
                "lidar"
            ]
        },
        {
            "topic": "Cybersecurity Zero Trust",
            "title": "Never Trust, Always Verify: The New Rules of Digital Warfare",
            "description": "How enterprise architectures defend against nation-state cyberattacks and ransomware.",
            "score": 8.7,
            "keywords": [
                "cybersecurity",
                "zero trust",
                "infosec"
            ]
        },
        {
            "topic": "Starlink Satellite Constellations",
            "title": "How 10,000 Low-Earth Orbit Satellites Beam Global Gigabit Internet",
            "description": "Laser inter-satellite links and phased-array beamforming antennas explained.",
            "score": 9.2,
            "keywords": [
                "starlink",
                "satellites",
                "telecom"
            ]
        },
        {
            "topic": "RISC-V Open Architecture",
            "title": "The Open-Source Chip Design Threatening Intel and ARM's Monopoly",
            "description": "Why royalty-free instruction set architectures are taking over tech hardware.",
            "score": 8.8,
            "keywords": [
                "risc v",
                "semiconductors",
                "chip design"
            ]
        },
        {
            "topic": "DNA Digital Data Storage",
            "title": "Storing the Entire Internet's Data in a Single Teaspoon of DNA",
            "description": "How synthetic DNA encodes petabytes of digital information for 10,000 years.",
            "score": 9.5,
            "keywords": [
                "dna storage",
                "biotech",
                "data storage"
            ]
        },
        {
            "topic": "Augmented Reality Glasses",
            "title": "Waveguides and Micro-OLED: The Optics Behind Future AR Glasses",
            "description": "How transparent diffractive waveguides project digital holographic screens onto your retina.",
            "score": 9.0,
            "keywords": [
                "augmented reality",
                "optics",
                "smart glasses"
            ]
        },
        {
            "topic": "Neuralink Brain Chips",
            "title": "The Ultra-Thin Electrodes Connecting Brain Neurons to Computers",
            "description": "Robotic surgical insertion of 1,024 flexible threads into the cerebral cortex.",
            "score": 9.4,
            "keywords": [
                "neuralink",
                "neurotech",
                "bci"
            ]
        },
        {
            "topic": "Blockchain Consensus",
            "title": "Proof of Work vs Proof of Stake: How Distributed Networks Agree Without a Boss",
            "description": "The cryptography and game theory preventing double-spending across global ledgers.",
            "score": 8.7,
            "keywords": [
                "blockchain",
                "cryptography",
                "distributed systems"
            ]
        },
        {
            "topic": "Micro-LED Displays",
            "title": "The Holy Grail of Displays: Why Micro-LED Beats OLED in Every Way",
            "description": "Microscopic inorganic LEDs that never burn in and deliver 10,000 nits of brightness.",
            "score": 8.9,
            "keywords": [
                "microled",
                "display tech",
                "hardware"
            ]
        },
        {
            "topic": "Supersonic Commercial Flight",
            "title": "The Return of Concorde: How NASA Muffled the Sonic Boom",
            "description": "Low-boom flight aerodynamics and the modern revival of supersonic passenger jets.",
            "score": 9.1,
            "keywords": [
                "supersonic flight",
                "aviation",
                "aerodynamics"
            ]
        },
        {
            "topic": "Deepfake Detection Tech",
            "title": "The AI Weaponry Detecting Synthesized Faces and Cloned Voices",
            "description": "Frequency domain analysis and eye-reflection forensics fighting synthetic media fraud.",
            "score": 9.0,
            "keywords": [
                "deepfakes",
                "ai security",
                "forensics"
            ]
        },
        {
            "topic": "Perovskite Solar Cells",
            "title": "The Miracle Crystal Boosting Solar Panel Efficiency Past 30%",
            "description": "Tandem silicon-perovskite solar cells revolutionizing clean energy economics.",
            "score": 9.2,
            "keywords": [
                "perovskite",
                "solar energy",
                "clean tech"
            ]
        },
        {
            "topic": "Nuclear Tokamak Magnets",
            "title": "High-Temperature Superconducting Magnets Taming 100-Million-Degree Plasma",
            "description": "How rare-earth barium copper oxide (REBCO) tapes shrink fusion reactors.",
            "score": 9.3,
            "keywords": [
                "fusion energy",
                "tokamak",
                "superconductors"
            ]
        },
        {
            "topic": "Neuromorphic Chips",
            "title": "Silicon Chips That Fire Like Biological Human Brain Neurons",
            "description": "Spiking neural networks that consume 1,000 times less power than standard GPUs.",
            "score": 9.1,
            "keywords": [
                "neuromorphic",
                "ai hardware",
                "brain chips"
            ]
        },
        {
            "topic": "Wi-Fi 7 vs 5G",
            "title": "Multi-Link Operation and 320 MHz Channels: The Speed of Wi-Fi 7",
            "description": "Why 40 Gbps wireless connectivity is replacing wired ethernet cables.",
            "score": 8.6,
            "keywords": [
                "wifi 7",
                "networking",
                "wireless"
            ]
        },
        {
            "topic": "Space Elevators Tech",
            "title": "Carbon Nanotube Ribbons: Can We Build an Elevator Into Orbit?",
            "description": "The material physics, counterweights, and space tether mechanics of space elevators.",
            "score": 9.2,
            "keywords": [
                "space elevator",
                "space exploration",
                "engineering"
            ]
        },
        {
            "topic": "Solid Oxide Fuel Cells",
            "title": "Turning Natural Gas and Hydrogen Into Electricity Without Burning",
            "description": "High-temperature electrochemical ceramic cells powering mega data centers.",
            "score": 8.8,
            "keywords": [
                "fuel cells",
                "hydrogen",
                "energy"
            ]
        },
        {
            "topic": "3D Bioprinting Organs",
            "title": "Printing Living Human Kidneys and Hearts Layer by Layer",
            "description": "Bio-inks, cellular scaffolding, and the quest to end organ transplant waitlists.",
            "score": 9.5,
            "keywords": [
                "3d bioprinting",
                "regenerative medicine",
                "biotech"
            ]
        },
        {
            "topic": "Quantum Key Distribution",
            "title": "The Unhackable Encryption Guaranteed by Quantum Physics Laws",
            "description": "Why eavesdropping on quantum photons alters their state and triggers alarms.",
            "score": 9.1,
            "keywords": [
                "quantum encryption",
                "cryptography",
                "qkd"
            ]
        },
        {
            "topic": "Carbon Nanotube Transistors",
            "title": "Surpassing Silicon: How Carbon Nanotubes Could Double Computer Speeds",
            "description": "Sub-1nm gate lengths and ballistic electron transport in carbon tubes.",
            "score": 8.9,
            "keywords": [
                "carbon nanotubes",
                "semiconductors",
                "computing"
            ]
        },
        {
            "topic": "High-Bandwidth Memory (HBM)",
            "title": "3D Stacked Silicon: The Secret Bottleneck Fueling the AI Chip Boom",
            "description": "Through-silicon vias (TSVs) packaging terabytes-per-second memory bandwidth.",
            "score": 9.0,
            "keywords": [
                "hbm memory",
                "ai hardware",
                "gpu"
            ]
        },
        {
            "topic": "Automated Code Generation",
            "title": "Will AI Software Engineers Replace Human Programmers by 2030?",
            "description": "LLM code synthesis, automated test generation, and autonomous agentic workflows.",
            "score": 9.2,
            "keywords": [
                "ai programming",
                "software engineering",
                "coding"
            ]
        },
        {
            "topic": "Lithium-Sulfur Batteries",
            "title": "Five Times the Energy Density of Lithium-Ion at Half the Cost",
            "description": "Overcoming the polysulfide shuttle effect for super lightweight aviation batteries.",
            "score": 9.0,
            "keywords": [
                "lithium sulfur",
                "batteries",
                "energy storage"
            ]
        },
        {
            "topic": "Self-Healing Materials",
            "title": "Micro-Capsules That Automatically Repair Scratches in Phones and Bridges",
            "description": "Polymers and vascular networks that bleed resin to heal cracks autonomously.",
            "score": 8.8,
            "keywords": [
                "self healing materials",
                "smart materials",
                "materials science"
            ]
        },
        {
            "topic": "Mesh Networking",
            "title": "Decentralized Communication Networks That Work When the Internet Goes Down",
            "description": "Peer-to-peer radio protocols hopping packets without cell towers or ISPs.",
            "score": 8.9,
            "keywords": [
                "mesh network",
                "networking",
                "telecom"
            ]
        },
        {
            "topic": "Homomorphic Encryption",
            "title": "Computing on Encrypted Data Without Ever Decrypting It First",
            "description": "The mathematical breakthrough allowing private cloud processing on sensitive data.",
            "score": 9.1,
            "keywords": [
                "homomorphic encryption",
                "cryptography",
                "privacy"
            ]
        },
        {
            "topic": "Direct Air Capture Tech",
            "title": "The Giant Fan Scrubbers Pulling 1 Million Tons of CO2 From the Sky",
            "description": "Solid sorbent filters and geothermal energy sequestering carbon underground in basalt.",
            "score": 8.8,
            "keywords": [
                "direct air capture",
                "climate tech",
                "carbon sequestration"
            ]
        },
        {
            "topic": "Vertical Farming Automation",
            "title": "Growing 100 Times More Food in Skyscrapers Using 95% Less Water",
            "description": "Aeroponic nutrient misting, automated robotic harvesting, and tailored LED spectrums.",
            "score": 9.0,
            "keywords": [
                "vertical farming",
                "agtech",
                "sustainability"
            ]
        },
        {
            "topic": "Silicon Photonics",
            "title": "Replacing Copper Wires With Beams of Light Inside Computer Chips",
            "description": "Optical transceivers on silicon transferring terabits at the speed of light.",
            "score": 9.1,
            "keywords": [
                "silicon photonics",
                "computing",
                "optics"
            ]
        },
        {
            "topic": "Autonomous Drone Swarms",
            "title": "How Thousands of Micro-Drones Coordinate Without a Central Leader",
            "description": "Flocking algorithms and decentralized collision avoidance inspired by bird murmurations.",
            "score": 9.3,
            "keywords": [
                "drone swarms",
                "robotics",
                "algorithms"
            ]
        },
        {
            "topic": "Smart Dust Sensors",
            "title": "Millimeter-Sized Micro-Computers Floating in the Atmosphere",
            "description": "MEMS wireless micro-electromechanical sensors monitoring climate and crop health.",
            "score": 8.9,
            "keywords": [
                "smart dust",
                "iot",
                "microtechnology"
            ]
        },
        {
            "topic": "Ultra-High Voltage DC Grids",
            "title": "Transmitting Gigawatts of Renewable Power Over 3,000 Kilometers",
            "description": "Why 1.1 Million-Volt DC power lines have less line loss than standard AC lines.",
            "score": 8.7,
            "keywords": [
                "uhv dc",
                "electrical grid",
                "renewable energy"
            ]
        },
        {
            "topic": "Memristors",
            "title": "The Missing Circuit Element That Remembers Electricity When Turned Off",
            "description": "Resistance switching oxides merging computer memory and processing into one device.",
            "score": 9.0,
            "keywords": [
                "memristor",
                "computer hardware",
                "nanotechnology"
            ]
        },
        {
            "topic": "Digital Twins",
            "title": "Creating Exact Virtual Clones of Jet Engines and Cities in Real-Time",
            "description": "Real-time telemetry and physics simulations predicting catastrophic failures before they happen.",
            "score": 8.8,
            "keywords": [
                "digital twin",
                "simulation",
                "industry 4.0"
            ]
        },
        {
            "topic": "Thermal Energy Storage",
            "title": "Storing Renewable Power in 1,000\u00b0C Molten Salt and Crushed Rock",
            "description": "Heat batteries storing excess solar and wind power for weeks at rock-bottom cost.",
            "score": 8.7,
            "keywords": [
                "thermal storage",
                "energy",
                "batteries"
            ]
        },
        {
            "topic": "Tidal Lagoon Turbines",
            "title": "Harnessing the Gravitational Pull of the Moon for Predictable Clean Power",
            "description": "Underwater tidal stream turbines capturing the unstoppable kinetic power of ocean tides.",
            "score": 8.8,
            "keywords": [
                "tidal energy",
                "ocean power",
                "clean energy"
            ]
        },
        {
            "topic": "Haptic Feedback Suits",
            "title": "Feeling Physical Touch and Textures Inside Virtual Reality Worlds",
            "description": "Piezoelectric actuators and pneumatic micro-bladders simulating physical weight and pain.",
            "score": 9.1,
            "keywords": [
                "haptics",
                "virtual reality",
                "metaverse"
            ]
        },
        {
            "topic": "Exascale Supercomputing",
            "title": "One Quintillion Calculations Per Second: How Frontier Simulates the Cosmos",
            "description": "1,000,000,000,000,000,000 operations per second unlocking molecular dynamics.",
            "score": 9.2,
            "keywords": [
                "supercomputers",
                "exascale",
                "hpc"
            ]
        },
        {
            "topic": "Bio-Hybrid Robotics",
            "title": "Robots Powered by Living Muscle Cells and Biological Hearts",
            "description": "Combining synthetic 3D skeletons with contractile rat heart muscle tissue.",
            "score": 9.3,
            "keywords": [
                "biohybrid robots",
                "bionics",
                "biotech"
            ]
        },
        {
            "topic": "Sodium-Ion Batteries",
            "title": "The Cheap Table-Salt Batteries Powering Budget Electric Cars",
            "description": "Replacing scarce lithium and cobalt with abundant, fire-safe sodium chemistry.",
            "score": 9.1,
            "keywords": [
                "sodium ion",
                "ev batteries",
                "energy"
            ]
        },
        {
            "topic": "Liquid Immersion Cooling",
            "title": "Submerging Blazing AI Supercomputers in Non-Conductive Mineral Oil",
            "description": "Eliminating loud server fans and slashing data center cooling electricity by 90%.",
            "score": 8.9,
            "keywords": [
                "immersion cooling",
                "data centers",
                "hardware"
            ]
        },
        {
            "topic": "Space-Based Solar Power",
            "title": "Giant Orbital Mirrors Beaming Sunlight as Microwaves Down to Earth",
            "description": "Harvesting uninterrupted 24/7 space solar radiation without clouds or night.",
            "score": 9.4,
            "keywords": [
                "space solar",
                "space tech",
                "energy"
            ]
        },
        {
            "topic": "Robotic Surgical Systems",
            "title": "Sub-Millimeter Microsurgery Controlled by AI Algorithms",
            "description": "Da Vinci surgical arms eliminating surgeon hand tremors for microvascular repair.",
            "score": 9.0,
            "keywords": [
                "surgical robots",
                "medtech",
                "robotics"
            ]
        },
        {
            "topic": "Gimbaled Rocket Landings",
            "title": "The Physics of Landing an Orbital Rocket Booster on a Floating Ocean Barge",
            "description": "Grid fins, supersonic retro-propulsion, and cold-gas thrusters mastering precision landings.",
            "score": 9.5,
            "keywords": [
                "spacex",
                "falcon 9",
                "aerospace"
            ]
        },
        {
            "topic": "Smart Contact Lenses",
            "title": "Transparent Displays and Glucose Monitors Built Directly Into Contact Lenses",
            "description": "Micro-scale antennas and micro-LEDs projecting AR notifications on your eyeball.",
            "score": 9.2,
            "keywords": [
                "smart contacts",
                "wearables",
                "biomedical"
            ]
        },
        {
            "topic": "Quantum Sensors",
            "title": "Detecting Underground Bunkers and Submarines Using Gravity Gradiometers",
            "description": "Cold-atom interferometry measuring minute variations in Earth's gravitational pull.",
            "score": 9.1,
            "keywords": [
                "quantum sensors",
                "quantum tech",
                "defense"
            ]
        },
        {
            "topic": "Hyperloop Vacuum Trains",
            "title": "Magnetic Levitation Trains Traveling at 1,000 km/h in Low-Pressure Tubes",
            "description": "Eliminating air friction to achieve airplane speeds on ground rails.",
            "score": 8.8,
            "keywords": [
                "hyperloop",
                "maglev",
                "transportation"
            ]
        },
        {
            "topic": "Synthetic Diamond Wafers",
            "title": "Growing Lab Diamonds for the Next Generation of Heat-Proof Microchips",
            "description": "Chemical vapor deposition (CVD) creating diamond thermal heat sinks for ultra-fast CPUs.",
            "score": 9.2,
            "keywords": [
                "synthetic diamonds",
                "semiconductors",
                "materials science"
            ]
        }
    ],
    "education": [
        {
            "topic": "The Feynman Technique",
            "title": "How to Learn Anything in 4 Steps by Teaching It to a Child",
            "description": "Nobel laureate Richard Feynman's simple mental framework for identifying gaps in knowledge.",
            "score": 9.4,
            "keywords": [
                "feynman technique",
                "learning hacks",
                "study tips"
            ]
        },
        {
            "topic": "Spaced Repetition Systems",
            "title": "Why Cramming Fails and How Spaced Intervals Lock Memories Forever",
            "description": "The algorithmic scheduling of reviews based on brain memory decay curves.",
            "score": 9.2,
            "keywords": [
                "spaced repetition",
                "anki",
                "memory"
            ]
        },
        {
            "topic": "The Method of Loci",
            "title": "Build a Mind Palace: How Memory Champions Memorize 1,000 Numbers",
            "description": "The ancient Greek technique of anchoring abstract facts to familiar spatial locations.",
            "score": 9.1,
            "keywords": [
                "mind palace",
                "memory loci",
                "mnemonics"
            ]
        },
        {
            "topic": "Pomodoro Science",
            "title": "Why Working in 25-Minute Sprints Prevents Mental Fatigue",
            "description": "The neurobiology of dopamine replenishment and attention endurance during structured intervals.",
            "score": 8.7,
            "keywords": [
                "pomodoro",
                "productivity",
                "focus"
            ]
        },
        {
            "topic": "Active Recall vs Re-reading",
            "title": "Stop Highlighting Books: The Brutal Truth About Study Habits",
            "description": "Why testing yourself creates neural pathways while passive highlighting produces an illusion of competence.",
            "score": 9.5,
            "keywords": [
                "active recall",
                "study methods",
                "learning"
            ]
        },
        {
            "topic": "Cognitive Load Theory",
            "title": "Why Your Brain Overheats When Trying to Learn Too Much at Once",
            "description": "Working memory limitations and how chunking complex material prevents cognitive overload.",
            "score": 8.9,
            "keywords": [
                "cognitive load",
                "psychology",
                "education"
            ]
        },
        {
            "topic": "Speed Reading Realities",
            "title": "Can You Really Read 1,000 Words a Minute Without Losing Comprehension?",
            "description": "Eye tracking studies on saccades, foveal vision, and why subvocalization is essential for deep reading.",
            "score": 9.0,
            "keywords": [
                "speed reading",
                "reading myths",
                "comprehension"
            ]
        },
        {
            "topic": "The Pareto Principle (80/20 Rule)",
            "title": "Mastering Any Skill by Finding the Critical 20% That Drives 80% of Results",
            "description": "How to deconstruct skills to reach professional competence in a fraction of the time.",
            "score": 9.3,
            "keywords": [
                "pareto principle",
                "skill acquisition",
                "efficiency"
            ]
        },
        {
            "topic": "Dual Coding Theory",
            "title": "Combining Words and Visuals to Double Your Brain's Retention",
            "description": "How verbal and visual cognitive channels process information independently and reinforce memory.",
            "score": 8.8,
            "keywords": [
                "dual coding",
                "learning styles",
                "visual learning"
            ]
        },
        {
            "topic": "Metacognition Skills",
            "title": "Thinking About Thinking: The Secret Habit of Top Performers",
            "description": "How self-monitoring and auditing your own reasoning prevents blind spots and cognitive errors.",
            "score": 9.0,
            "keywords": [
                "metacognition",
                "critical thinking",
                "self improvement"
            ]
        },
        {
            "topic": "Deliberate Practice Framework",
            "title": "Why 10,000 Hours Means Nothing Without Immediate Feedback",
            "description": "Anders Ericsson's breakthrough research on how elite musicians and grandmasters actually practice.",
            "score": 9.4,
            "keywords": [
                "deliberate practice",
                "mastery",
                "skill building"
            ]
        },
        {
            "topic": "The Cornell Note-Taking System",
            "title": "The Note-Taking Structure Used by Ivy League Students",
            "description": "Dividing pages into cues, notes, and summaries to create self-contained review sheets.",
            "score": 8.9,
            "keywords": [
                "cornell notes",
                "note taking",
                "study habits"
            ]
        },
        {
            "topic": "The Forgetting Curve",
            "title": "Hermann Ebbinghaus: Why We Forget 70% of What We Learn in 24 Hours",
            "description": "The exponential decay of memory traces and the exact math behind long-term retention.",
            "score": 9.2,
            "keywords": [
                "forgetting curve",
                "memory science",
                "ebbinghaus"
            ]
        },
        {
            "topic": "Interleaving Practice",
            "title": "Why Mixing Different Problems Beats Practicing One Thing Repeatedly",
            "description": "How interleaving forces the brain to classify problem types rather than running on autopilot.",
            "score": 9.0,
            "keywords": [
                "interleaving",
                "practice methods",
                "education"
            ]
        },
        {
            "topic": "Growth Mindset vs Fixed Mindset",
            "title": "Carol Dweck's Stanford Research on Why Beliefs Shape Intelligence",
            "description": "How praising effort over innate talent changes brain plasticity and resilience to failure.",
            "score": 9.1,
            "keywords": [
                "growth mindset",
                "psychology",
                "dweck"
            ]
        },
        {
            "topic": "Bloom's Revised Taxonomy",
            "title": "From Memorization to Creation: The 6 Levels of True Understanding",
            "description": "How to move beyond surface-level recall to analytical and creative mastery.",
            "score": 8.7,
            "keywords": [
                "blooms taxonomy",
                "pedagogy",
                "deep learning"
            ]
        },
        {
            "topic": "Debunking Learning Styles",
            "title": "The Scientific Myth of Visual, Auditory, and Kinesthetic Learners",
            "description": "Why cognitive psychologists found zero evidence that matching teaching to learning styles improves test scores.",
            "score": 9.3,
            "keywords": [
                "learning styles myth",
                "educational psychology",
                "debunked"
            ]
        },
        {
            "topic": "The SQ3R Reading Method",
            "title": "Survey, Question, Read, Recite, Review: How to Devour Hard Textbooks",
            "description": "The military-proven systematic method for extracting and retaining dense academic material.",
            "score": 8.8,
            "keywords": [
                "sq3r",
                "reading comprehension",
                "academic success"
            ]
        },
        {
            "topic": "Spaced Retrieval Practice",
            "title": "The Testing Effect: How Taking Quizzes Physically Alters Brain Synapses",
            "description": "Why retrieval strengthens memory connections far more than re-studying existing notes.",
            "score": 9.1,
            "keywords": [
                "retrieval practice",
                "testing effect",
                "brain plasticity"
            ]
        },
        {
            "topic": "Active Listening Framework",
            "title": "How to Remember Every Conversation and Lecture Without Notes",
            "description": "Paraphrasing, non-verbal mirroring, and identifying underlying themes in real time.",
            "score": 8.6,
            "keywords": [
                "active listening",
                "communication",
                "soft skills"
            ]
        },
        {
            "topic": "Sleep and Memory Consolidation",
            "title": "What Happens in Your Brain When You Sleep After Studying",
            "description": "How slow-wave sleep and sleep spindles replay daily memories to transfer them into long-term cortex storage.",
            "score": 9.5,
            "keywords": [
                "sleep science",
                "memory consolidation",
                "neuroscience"
            ]
        },
        {
            "topic": "First Principles Thinking",
            "title": "Elon Musk & Aristotle: How to Solve Impossible Problems From Scratch",
            "description": "Boiling a system down to its most fundamental truths and reasoning up from there.",
            "score": 9.4,
            "keywords": [
                "first principles",
                "problem solving",
                "mental models"
            ]
        },
        {
            "topic": "How to Read a Scientific Paper",
            "title": "The Non-Linear Method Scientists Use to Read Research Papers in 20 Minutes",
            "description": "Deconstructing abstracts, methodology, sample sizes, and p-hacking in academic literature.",
            "score": 9.1,
            "keywords": [
                "scientific papers",
                "research methods",
                "critical thinking"
            ]
        },
        {
            "topic": "Deep Work by Cal Newport",
            "title": "How to Cultivate 4 Hours of Unbroken Focus in a World of Distractions",
            "description": "The cognitive cost of context switching and how attentional residue ruins high-value output.",
            "score": 9.3,
            "keywords": [
                "deep work",
                "productivity",
                "focus"
            ]
        },
        {
            "topic": "Socratic Questioning Method",
            "title": "How Socrates Disarmed Arrogant Politicians With 6 Simple Questions",
            "description": "Using disciplined questions to uncover hidden assumptions, evidence, and implications.",
            "score": 8.9,
            "keywords": [
                "socratic method",
                "critical thinking",
                "philosophy"
            ]
        },
        {
            "topic": "Associative Memory Hacks",
            "title": "Peg Systems and Major System: Memorizing Complex Codes Effortlessly",
            "description": "Converting numbers into phonetic consonant sounds and vivid mental imagery.",
            "score": 8.8,
            "keywords": [
                "major system",
                "memory hacks",
                "phonetics"
            ]
        },
        {
            "topic": "Ultralearning Principles",
            "title": "How Scott Young Learned 4 Years of MIT Computer Science in 12 Months",
            "description": "Directness, drilling, and metalearning strategies for aggressive self-directed education.",
            "score": 9.4,
            "keywords": [
                "ultralearning",
                "self education",
                "accelerated learning"
            ]
        },
        {
            "topic": "Exam Priming and Anxiety",
            "title": "How to Turn Pre-Test Panic Into Peak Performance Focus",
            "description": "Reappraising adrenaline arousal as excitement to prevent working memory choking.",
            "score": 8.9,
            "keywords": [
                "test anxiety",
                "performance psychology",
                "stress management"
            ]
        },
        {
            "topic": "Mind Mapping Science",
            "title": "Tony Buzan's Radial Thinking: Why Hierarchical Trees Boost Creativity",
            "description": "Organizing ideas visually around a central concept with colors and branches.",
            "score": 8.7,
            "keywords": [
                "mind mapping",
                "brainstorming",
                "visual thinking"
            ]
        },
        {
            "topic": "The Chunking Method",
            "title": "How Chess Grandmasters See Boards in Chunks of 50,000 Patterns",
            "description": "Compressing disparate bits of information into meaningful perceptual units.",
            "score": 9.2,
            "keywords": [
                "chunking",
                "cognitive psychology",
                "expertise"
            ]
        },
        {
            "topic": "Leitner Flashcard Box System",
            "title": "The Analog Flashcard Method That Powers Modern Digital Learning",
            "description": "Sorting flashcards into 5 physical boxes based on mastery to optimize review frequency.",
            "score": 8.8,
            "keywords": [
                "leitner box",
                "flashcards",
                "study systems"
            ]
        },
        {
            "topic": "BDNF and Physical Exercise",
            "title": "Why 20 Minutes of Cardio Before Studying Supercharges Brain Growth",
            "description": "Brain-Derived Neurotrophic Factor: the biological fertilizer for synaptogenesis.",
            "score": 9.3,
            "keywords": [
                "bdnf",
                "exercise brain",
                "neurogenesis"
            ]
        },
        {
            "topic": "Handwriting vs Typing Notes",
            "title": "Why Taking Notes by Hand Beats Laptops for Conceptual Understanding",
            "description": "The cognitive filtering required when handwriting forces synthesis rather than verbatim transcription.",
            "score": 9.2,
            "keywords": [
                "handwriting notes",
                "laptop study",
                "cognition"
            ]
        },
        {
            "topic": "The Prot\u00e9g\u00e9 Effect",
            "title": "Why You Master Topics Twice as Fast When Preparing to Teach Others",
            "description": "The psychological commitment that forces deeper organization and error checking.",
            "score": 9.1,
            "keywords": [
                "protege effect",
                "peer teaching",
                "learning"
            ]
        },
        {
            "topic": "Polymath Learning Strategy",
            "title": "How Leonardo da Vinci Combined Art, Engineering, and Anatomy",
            "description": "Cross-pollinating diverse fields to generate groundbreaking innovations at the intersections.",
            "score": 9.0,
            "keywords": [
                "polymath",
                "da vinci",
                "interdisciplinary"
            ]
        },
        {
            "topic": "Speed Math Mental Tricks",
            "title": "Calculate 97 x 96 in Your Head in 3 Seconds Flat",
            "description": "Vedic mathematics and algebraic shortcuts for lightning-fast mental arithmetic.",
            "score": 9.2,
            "keywords": [
                "speed math",
                "vedic math",
                "mental arithmetic"
            ]
        },
        {
            "topic": "Language Immersion Without Traveling",
            "title": "How Polyglots Become Fluent in 6 Months From Their Bedrooms",
            "description": "Comprehensible input hypothesis (Stephen Krashen) and sentence-mining methods.",
            "score": 9.3,
            "keywords": [
                "language learning",
                "polyglot",
                "comprehensible input"
            ]
        },
        {
            "topic": "Audio-Assisted Reading",
            "title": "Listening at 2x Speed While Reading: The Secret to High-Retention Immersion",
            "description": "Engaging both visual and auditory processing loops simultaneously to eliminate mind wandering.",
            "score": 8.9,
            "keywords": [
                "audiobooks",
                "bimodal reading",
                "retention"
            ]
        },
        {
            "topic": "Breaking Skill Plateaus",
            "title": "The OK Plateau: Why Most People Stop Improving After 50 Hours",
            "description": "How deliberate disruption of automated motor and cognitive habits restarts progression.",
            "score": 9.1,
            "keywords": [
                "skill plateau",
                "mastery",
                "deliberate practice"
            ]
        },
        {
            "topic": "Cognitive Reframing of Failure",
            "title": "How Nobel Winners Treat Failed Experiments as Free Data",
            "description": "Shifting identity from 'I am a failure' to 'This hypothesis produced negative results'.",
            "score": 8.8,
            "keywords": [
                "reframing",
                "mindset",
                "resilience"
            ]
        },
        {
            "topic": "Active Summarization",
            "title": "The One-Sentence Rule for Retaining Entire Chapters of Books",
            "description": "Distilling complex chapters into single actionable core principles in your own words.",
            "score": 8.7,
            "keywords": [
                "summarization",
                "reading comprehension",
                "synthesis"
            ]
        },
        {
            "topic": "Curiosity-Driven Exploration",
            "title": "The Information Gap Theory of Curiosity: Why Intrigue Powers Recall",
            "description": "George Loewenstein's research on how identifying knowledge gaps triggers dopamine seeking.",
            "score": 9.0,
            "keywords": [
                "curiosity",
                "information gap",
                "intrinsic motivation"
            ]
        },
        {
            "topic": "Self-Regulated Learning Systems",
            "title": "The Three Phases of High-Achieving Autonomous Students",
            "description": "Forethought, performance monitoring, and self-reflection in self-directed education.",
            "score": 8.8,
            "keywords": [
                "self regulated learning",
                "study systems",
                "autonomy"
            ]
        },
        {
            "topic": "Overcoming Procrastination Psychology",
            "title": "Why Procrastination is an Emotional Problem, Not a Time Management Issue",
            "description": "Tim Pychyl's research on amygdala hijack and short-term mood repair vs long-term goals.",
            "score": 9.5,
            "keywords": [
                "procrastination",
                "emotional regulation",
                "productivity"
            ]
        },
        {
            "topic": "Second Brain PKM Systems",
            "title": "Building a Personal Knowledge Management System With Linked Notes",
            "description": "Tiago Forte's CODE method (Capture, Organize, Distill, Express) and Zettelkasten note-taking.",
            "score": 9.1,
            "keywords": [
                "second brain",
                "zettelkasten",
                "knowledge management"
            ]
        },
        {
            "topic": "Critical Thinking Heuristics",
            "title": "Occam's Razor, Hanlon's Razor, and 5 Mental Models to Spot Fallacies",
            "description": "Essential philosophical tools to evaluate claims and dismantle weak arguments.",
            "score": 9.2,
            "keywords": [
                "occams razor",
                "mental models",
                "critical thinking"
            ]
        },
        {
            "topic": "The Generation Effect",
            "title": "Why Generating an Answer Beats Reading the Solution Every Time",
            "description": "How the brain forms stronger synapses when forced to synthesize an answer independently.",
            "score": 8.9,
            "keywords": [
                "generation effect",
                "memory retrieval",
                "study hacks"
            ]
        },
        {
            "topic": "Visual Note-Taking (Sketchnoting)",
            "title": "Doodling to Remember: The Science of Visual Sketchnoting",
            "description": "Translating spoken lectures into icons, arrows, and relational spatial layouts.",
            "score": 8.8,
            "keywords": [
                "sketchnoting",
                "doodling",
                "visual notes"
            ]
        },
        {
            "topic": "Spaced Vocabulary Acquisition",
            "title": "How to Learn 30 New Foreign Words Every Day and Never Forget Them",
            "description": "Contextual mnemonics, frequency dictionaries, and graduated interval review.",
            "score": 9.0,
            "keywords": [
                "vocabulary",
                "language learning",
                "mnemonics"
            ]
        },
        {
            "topic": "Socratic Debate in Education",
            "title": "Why Debating the Opposite Side Makes You 10x Smarter",
            "description": "Steelmanning opposing viewpoints to eliminate cognitive bias and strengthen understanding.",
            "score": 9.1,
            "keywords": [
                "steelmanning",
                "debate",
                "intellectual humility"
            ]
        },
        {
            "topic": "The Pygmalion Effect in Schools",
            "title": "How Teacher Expectations Silently Alter Student IQ Scores",
            "description": "The self-fulfilling prophecy of high expectations proven in educational psychology.",
            "score": 8.9,
            "keywords": [
                "pygmalion effect",
                "teacher expectations",
                "psychology"
            ]
        },
        {
            "topic": "Cognitive Reserve Building",
            "title": "Lifelong Learning as a Shield Against Cognitive Decline and Dementia",
            "description": "How bilingualism and musical instruments build complex neural backup networks in the brain.",
            "score": 9.2,
            "keywords": [
                "cognitive reserve",
                "brain health",
                "lifelong learning"
            ]
        }
    ],
    "history": [
        {
            "topic": "Fall of the Roman Empire",
            "title": "The Real Reasons Rome Collapsed (It Wasn't Just Barbarians)",
            "description": "Hyperinflation, political corruption, plague, and military overextension dismantling the empire.",
            "score": 9.4,
            "keywords": [
                "rome",
                "roman empire",
                "ancient history"
            ]
        },
        {
            "topic": "Library of Alexandria",
            "title": "The Truth About the Burning of the Library of Alexandria",
            "description": "How centuries of budget cuts, Caesar's fire, and political neglect eroded humanity's greatest library.",
            "score": 9.2,
            "keywords": [
                "alexandria",
                "ancient world",
                "historical myths"
            ]
        },
        {
            "topic": "Bronze Age Collapse",
            "title": "1177 BC: The Year Almost Every Mediterranean Civilization Vanished",
            "description": "The mysterious Sea Peoples, megadroughts, and systemic trade collapse destroying ancient empires.",
            "score": 9.6,
            "keywords": [
                "bronze age",
                "sea peoples",
                "ancient collapse"
            ]
        },
        {
            "topic": "The Voynich Manuscript",
            "title": "The 600-Year-Old Book That No Cryptographer Can Decipher",
            "description": "Carbon-dated 15th-century parchment filled with unknown botanical drawings and undeciphered script.",
            "score": 9.5,
            "keywords": [
                "voynich manuscript",
                "cryptography",
                "historical mystery"
            ]
        },
        {
            "topic": "Pyramids of Giza Engineering",
            "title": "How the Ancient Egyptians Moved 2.3 Million 2-Ton Stones",
            "description": "The Wadi al-Jarf papyri discovery revealing canals, sledges, and workforce logistics.",
            "score": 9.5,
            "keywords": [
                "pyramids",
                "ancient egypt",
                "engineering history"
            ]
        },
        {
            "topic": "Apollo 11 AGC Computer",
            "title": "The 70-Pound Computer With 4KB of RAM That Landed Humans on the Moon",
            "description": "Margaret Hamilton's rope-core memory software that saved Neil Armstrong from an abort crash.",
            "score": 9.3,
            "keywords": [
                "apollo 11",
                "nasa",
                "moon landing"
            ]
        },
        {
            "topic": "The Silk Road Revolution",
            "title": "The 4,000-Mile Trade Highway That Connected Ancient Rome to China",
            "description": "How the exchange of silk, spices, religions, paper, and plagues forged the modern global economy.",
            "score": 9.0,
            "keywords": [
                "silk road",
                "trade routes",
                "world history"
            ]
        },
        {
            "topic": "The Industrial Revolution",
            "title": "How Coal and Steam Changed Human History in 50 Short Years",
            "description": "The explosive shift from agrarian muscle power to fossil-fueled industrial machines.",
            "score": 9.1,
            "keywords": [
                "industrial revolution",
                "steam engine",
                "economic history"
            ]
        },
        {
            "topic": "Deciphering the Rosetta Stone",
            "title": "The 20-Year Linguistic Battle to Read Egyptian Hieroglyphs",
            "description": "How Jean-Fran\u00e7ois Champollion cracked the royal cartouches and unlocked 3,000 years of history.",
            "score": 9.0,
            "keywords": [
                "rosetta stone",
                "hieroglyphs",
                "linguistics"
            ]
        },
        {
            "topic": "The Black Death (1347-1351)",
            "title": "How the Plague Wiped Out Half of Europe and Created the Middle Class",
            "description": "Labor shortages, collapsing feudalism, and wage surges sparked by Yersinia pestis.",
            "score": 9.4,
            "keywords": [
                "black death",
                "bubonic plague",
                "medieval history"
            ]
        },
        {
            "topic": "Gutenberg's Printing Press",
            "title": "The Wine Press Invention That Sparked Religious Revolutions and Modern Science",
            "description": "Movable metal type democratization of knowledge breaking clerical monopolies.",
            "score": 9.2,
            "keywords": [
                "gutenberg",
                "printing press",
                "renaissance"
            ]
        },
        {
            "topic": "Battle of Thermopylae Reality",
            "title": "The 300 Spartans: What Hollywood Movies Left Out About the Battle",
            "description": "The 7,000 Greek allies, Phocian goat path betrayal, and tactical realities of the Persian invasion.",
            "score": 9.3,
            "keywords": [
                "sparta",
                "thermopylae",
                "ancient greece"
            ]
        },
        {
            "topic": "Mayan Civilization Collapse",
            "title": "Why Did Millions of Mayans Suddenly Abandon Their Mega-Cities?",
            "description": "Sediment core evidence proving multi-decade megadroughts and deforestation shattered the Mayan cities.",
            "score": 9.2,
            "keywords": [
                "maya",
                "archaeology",
                "ancient history"
            ]
        },
        {
            "topic": "The Manhattan Project",
            "title": "How 130,000 People Kept the Atomic Bomb a Secret For 3 Years",
            "description": "Oak Ridge enrichment, Los Alamos theoretical physics, and Klaus Fuchs espionage.",
            "score": 9.4,
            "keywords": [
                "manhattan project",
                "atomic bomb",
                "ww2"
            ]
        },
        {
            "topic": "Islamic Golden Age Inventions",
            "title": "When Baghdad Was the Intellectual Capital of the Entire Planet",
            "description": "House of Wisdom scholars inventing algebra, modern optics, distillation, and peer-reviewed medicine.",
            "score": 9.3,
            "keywords": [
                "islamic golden age",
                "house of wisdom",
                "science history"
            ]
        },
        {
            "topic": "French Revolution Catalysts",
            "title": "Bread Shortages, Famine, and the Fall of the Bastille",
            "description": "How volcanic eruptions in Iceland caused failed harvests that triggered the 1789 revolution.",
            "score": 9.0,
            "keywords": [
                "french revolution",
                "bastille",
                "european history"
            ]
        },
        {
            "topic": "Viking Vinland Settlement",
            "title": "L'Anse aux Meadows: Vikings in North America 500 Years Before Columbus",
            "description": "Norse timber camps in Newfoundland and archaeological evidence of transatlantic voyages.",
            "score": 9.2,
            "keywords": [
                "vikings",
                "vinland",
                "exploration"
            ]
        },
        {
            "topic": "Code of Hammurabi",
            "title": "An Eye for an Eye: The World's First Written Legal System",
            "description": "Babylonian stone stele carving 282 edicts defining property rights, contracts, and punishments.",
            "score": 8.8,
            "keywords": [
                "hammurabi",
                "babylon",
                "legal history"
            ]
        },
        {
            "topic": "The Renaissance Explosion",
            "title": "How Black Death Survivor Wealth and Greek Refugees Sparked Florence",
            "description": "Fall of Constantinople scholars fleeing to Italy with classical manuscripts under Medici patronage.",
            "score": 9.1,
            "keywords": [
                "renaissance",
                "florence",
                "art history"
            ]
        },
        {
            "topic": "Untold Sinking of the Titanic",
            "title": "The Coal Fire and Rivet Flaws That Sealed the Titanic's Fate",
            "description": "Metallurgical analysis of brittle low-grade steel rivets and bunker smolder before departure.",
            "score": 9.3,
            "keywords": [
                "titanic",
                "maritime disaster",
                "history"
            ]
        },
        {
            "topic": "The Mongol Yam Relay",
            "title": "The World's Fastest Pony Express That Governed One Fifth of Earth",
            "description": "Genghis Khan's 50,000-horse relay network delivering messages 200 miles per day.",
            "score": 9.1,
            "keywords": [
                "mongol empire",
                "genghis khan",
                "communication"
            ]
        },
        {
            "topic": "Cold War Space Race",
            "title": "From Sputnik to Apollo: The Secret Spy Satellite War Behind the Moon Race",
            "description": "Corona reconnaissance capsules and ballistic missile rocketry disguised as scientific prestige.",
            "score": 9.2,
            "keywords": [
                "space race",
                "cold war",
                "space history"
            ]
        },
        {
            "topic": "Origins of the Internet",
            "title": "ARPANET (1969): The Cold War Military Network That Connected 4 Computers",
            "description": "Packet-switching networks designed to survive nuclear strikes becoming the World Wide Web.",
            "score": 9.1,
            "keywords": [
                "arpanet",
                "internet history",
                "tech history"
            ]
        },
        {
            "topic": "1918 Spanish Flu Pandemic",
            "title": "Why the Deadliest Flu in History Was Censored by World War I Armies",
            "description": "H1N1 virus killing 50 million people while wartime censors kept news off front pages.",
            "score": 9.0,
            "keywords": [
                "1918 flu",
                "pandemic history",
                "ww1"
            ]
        },
        {
            "topic": "Samurai Code of Bushido",
            "title": "The Myth vs Reality of Feudal Japan's Warrior Aristocracy",
            "description": "From battlefield archers to bureaucratic samurai poets during the Tokugawa peace.",
            "score": 9.0,
            "keywords": [
                "samurai",
                "bushido",
                "japan history"
            ]
        },
        {
            "topic": "Fall of Constantinople (1453)",
            "title": "Mehmed II and the Super-Cannons That Shattered the Theodosian Walls",
            "description": "Urban the engineer's giant bronze bombard breaching 1,000-year-old Roman fortifications.",
            "score": 9.3,
            "keywords": [
                "constantinople",
                "ottoman empire",
                "byzantine"
            ]
        },
        {
            "topic": "Neolithic Agricultural Revolution",
            "title": "The Accidental Discovery That Ended 200,000 Years of Hunting and Gathering",
            "description": "Domestication of wheat, barley, and cattle in the Fertile Crescent giving birth to cities.",
            "score": 9.1,
            "keywords": [
                "agriculture",
                "neolithic",
                "anthropology"
            ]
        },
        {
            "topic": "Transatlantic Telegraph Cable",
            "title": "Connecting London and New York by Cable in 1858 Under 2 Miles of Ocean",
            "description": "Cyrus Field's engineering triumph reducing transatlantic communication from 10 days to 17 hours.",
            "score": 8.9,
            "keywords": [
                "telegraph",
                "victorian engineering",
                "telecom"
            ]
        },
        {
            "topic": "Cracking the Enigma Machine",
            "title": "Alan Turing and the Bletchley Park Bombe Machines",
            "description": "How Polish mathematicians and British cryptanalysts shortened World War II by 2 years.",
            "score": 9.5,
            "keywords": [
                "enigma",
                "alan turing",
                "bletchley park"
            ]
        },
        {
            "topic": "Building the Panama Canal",
            "title": "Yellow Fever, Dynamite, and 25,000 Deaths: Slicing a Continent in Two",
            "description": "William Gorgas eradicating mosquitoes and Goethals building lock gates to link oceans.",
            "score": 9.0,
            "keywords": [
                "panama canal",
                "engineering",
                "history"
            ]
        },
        {
            "topic": "The Magna Carta (1215)",
            "title": "Why English Barons Cornered King John at Runnymede",
            "description": "The peace treaty that first established that the king was not above the law.",
            "score": 8.8,
            "keywords": [
                "magna carta",
                "constitutional history",
                "medieval"
            ]
        },
        {
            "topic": "Roman Concrete Longevity",
            "title": "Why 2,000-Year-Old Roman Harbors Are Growing Stronger in Seawater",
            "description": "Volcanic ash pozzolana, quicklime clasts, and self-healing aluminum tobermorite crystals.",
            "score": 9.4,
            "keywords": [
                "roman concrete",
                "ancient engineering",
                "materials"
            ]
        },
        {
            "topic": "Great Wall of China Construction",
            "title": "Sticky Rice Mortar and 2,000 Years of Defensive Fortifications",
            "description": "How multiple dynasties built 13,000 miles of ram-earth and stone ramparts against northern nomads.",
            "score": 9.1,
            "keywords": [
                "great wall",
                "chinese history",
                "ancient monuments"
            ]
        },
        {
            "topic": "Invention of Gunpowder",
            "title": "Chinese Alchemists Looking for Immortality Elixir Invented Gunpowder",
            "description": "Saltpeter, sulfur, and charcoal transforming warfare from fire lances to modern artillery.",
            "score": 9.2,
            "keywords": [
                "gunpowder",
                "alchemy",
                "military history"
            ]
        },
        {
            "topic": "US Constitutional Convention",
            "title": "The Secret Philadelphia Summer of 1787 That Created a New Republic",
            "description": "The Great Compromise, federalist papers, and contentious debates behind the US Constitution.",
            "score": 8.9,
            "keywords": [
                "constitution",
                "american history",
                "founding fathers"
            ]
        },
        {
            "topic": "Golden Gate Bridge Engineering",
            "title": "Building Across Treacherous 60mph Currents and Roaring Ocean Fog",
            "description": "Joseph Strauss, safety nets, and underwater diving caissons creating the San Francisco icon.",
            "score": 9.0,
            "keywords": [
                "golden gate bridge",
                "civil engineering",
                "american history"
            ]
        },
        {
            "topic": "The Meiji Restoration",
            "title": "How Japan Transformed From Feudal Isolation to Industrial Superpower in 40 Years",
            "description": "The arrival of Matthew Perry's black ships forcing modernization, railways, and telegraphs.",
            "score": 9.2,
            "keywords": [
                "meiji restoration",
                "japan history",
                "modernization"
            ]
        },
        {
            "topic": "Magellan's Circumnavigation",
            "title": "The Brutal 3-Year Voyage That Proved Earth Was a Single Ocean",
            "description": "Mutinies, scurvy, and the death of Magellan before the Victoria limped back to Spain.",
            "score": 9.1,
            "keywords": [
                "magellan",
                "circumnavigation",
                "age of discovery"
            ]
        },
        {
            "topic": "Alexander Fleming's Penicillin",
            "title": "The Moldy Petri Dish That Saved 200 Million Human Lives",
            "description": "How an untidy London lab desk led to the discovery of the world's first true antibiotic.",
            "score": 9.4,
            "keywords": [
                "penicillin",
                "medical history",
                "fleming"
            ]
        },
        {
            "topic": "Birth of Athenian Democracy",
            "title": "Cleisthenes: How Ancient Athens Replaced Tyranny With Citizens' Lotteries",
            "description": "Sortition, ostracism potsherds, and the assembly debates on the Pnyx hill.",
            "score": 8.9,
            "keywords": [
                "athenian democracy",
                "ancient greece",
                "political history"
            ]
        },
        {
            "topic": "The Terracotta Army",
            "title": "8,000 Life-Sized Clay Soldiers Buried to Guard China's First Emperor",
            "description": "Qin Shi Huang's subterranean mausoleum with flowing rivers of liquid mercury.",
            "score": 9.3,
            "keywords": [
                "terracotta army",
                "qin shi huang",
                "archaeology"
            ]
        },
        {
            "topic": "Rise and Fall of the Berlin Wall",
            "title": "From Barbed Wire Overnight to Sledgehammers in November 1989",
            "description": "G\u00fcnter Schabowski's accidental press conference announcement that dissolved the Iron Curtain.",
            "score": 9.3,
            "keywords": [
                "berlin wall",
                "cold war",
                "germany"
            ]
        },
        {
            "topic": "Inca Empire Road System (Qhapaq \u00d1an)",
            "title": "Building 25,000 Miles of Paved Highway Across 15,000-Foot Andean Peaks",
            "description": "Rope suspension bridges and Chasqui runners delivering fish across mountains in 24 hours.",
            "score": 9.1,
            "keywords": [
                "inca empire",
                "qhapaq nan",
                "ancient civil engineering"
            ]
        },
        {
            "topic": "Origins of Cuneiform Writing",
            "title": "How Sumerian Accountants Invented Writing to Track Barley and Beer",
            "description": "Clay bullae tokens evolving into wedge-shaped reed impressions in Mesopotamia.",
            "score": 8.9,
            "keywords": [
                "cuneiform",
                "sumer",
                "invention of writing"
            ]
        },
        {
            "topic": "Treaty of Versailles (1919)",
            "title": "The Flawed Peace Treaty That Guaranteed World War II",
            "description": "War guilt clauses, reparations, and Keynes' prediction of economic disaster.",
            "score": 9.0,
            "keywords": [
                "treaty of versailles",
                "ww1",
                "ww2 causes"
            ]
        },
        {
            "topic": "Discovery of King Tut's Tomb",
            "title": "Howard Carter: 'Can You See Anything?' 'Yes, Wonderful Things!'",
            "description": "The 1922 unearthing of KV62 in the Valley of the Kings and the curse myths that followed.",
            "score": 9.2,
            "keywords": [
                "tutankhamun",
                "egyptology",
                "howard carter"
            ]
        },
        {
            "topic": "James Watt's Steam Engine Condenser",
            "title": "The Separate Condenser Patent That Multiplied Engine Power by 400%",
            "description": "Transforming Newcomen's wasteful mine pump into the universal factory engine.",
            "score": 8.8,
            "keywords": [
                "james watt",
                "steam engine",
                "industrial tech"
            ]
        },
        {
            "topic": "The Enlightenment Thinkers",
            "title": "Voltaire, Locke, and Rousseau: The Coffeehouse Philosophers Who Changed Government",
            "description": "Challenging the divine right of kings with natural rights and separation of powers.",
            "score": 8.9,
            "keywords": [
                "enlightenment",
                "philosophy",
                "political theory"
            ]
        },
        {
            "topic": "Battle of Midway (1942)",
            "title": "5 Fatal Minutes: How US Dive Bombers Sunk 3 Japanese Aircraft Carriers",
            "description": "Naval intelligence breaking JN-25 codes and ambushing the Imperial Japanese Fleet.",
            "score": 9.4,
            "keywords": [
                "midway",
                "ww2",
                "naval warfare"
            ]
        },
        {
            "topic": "Cai Lun and Invention of Paper",
            "title": "How Mulberry Bark and Old Rags Replaced Heavy Bamboo Scrolls",
            "description": "Han Dynasty papermaking spreading along the Silk Road to transform global literacy.",
            "score": 8.9,
            "keywords": [
                "paper invention",
                "chinese history",
                "literacy"
            ]
        },
        {
            "topic": "The Transcontinental Railroad",
            "title": "The Golden Spike: Linking the Atlantic and Pacific Across 1,900 Miles",
            "description": "Central Pacific and Union Pacific crews blasting through Sierra granite with nitroglycerin.",
            "score": 9.0,
            "keywords": [
                "transcontinental railroad",
                "railroads",
                "us history"
            ]
        },
        {
            "topic": "The Hanseatic League",
            "title": "The Medieval Guild of German Merchants That Controlled Baltic Trade",
            "description": "Private commercial alliances waging wars, signing treaties, and establishing free trade cities.",
            "score": 8.8,
            "keywords": [
                "hanseatic league",
                "medieval trade",
                "commerce"
            ]
        }
    ],
    "nature": [
        {
            "topic": "Bioluminescent Oceans",
            "title": "Why the Sea Glows Electric Blue When Disturbed at Night",
            "description": "Dinoflagellate plankton triggering luciferase chemical reactions as a burglar alarm against predators.",
            "score": 9.3,
            "keywords": [
                "bioluminescence",
                "marine biology",
                "ocean"
            ]
        },
        {
            "topic": "Tardigrade Survival Powers",
            "title": "Water Bears: The Microscopic Animals That Can Survive Outer Space",
            "description": "Cryptobiosis, DNA-protecting Dsup proteins, and surviving 300\u00b0C heat and cosmic radiation.",
            "score": 9.5,
            "keywords": [
                "tardigrade",
                "microbiology",
                "extremophiles"
            ]
        },
        {
            "topic": "The Wood Wide Web",
            "title": "Trees Talk to Each Other: The Underground Fungal Internet",
            "description": "Mycorrhizal networks sharing carbon, phosphorus, and pest warning signals between mother trees and saplings.",
            "score": 9.4,
            "keywords": [
                "mycelium",
                "forest ecology",
                "trees"
            ]
        },
        {
            "topic": "Mariana Trench Creatures",
            "title": "What Lives 36,000 Feet Down Under 1,000 Atmospheres of Crushing Pressure?",
            "description": "Snailfish with pressure-adapted piezolytes, translucent flesh, and hydrothermal vent shrimp.",
            "score": 9.6,
            "keywords": [
                "mariana trench",
                "deep ocean",
                "abyss"
            ]
        },
        {
            "topic": "Monarch Butterfly Migration",
            "title": "The 3,000-Mile Journey Made by Creatures That Have Never Been There Before",
            "description": "How a multi-generational navigational compass guided by sun polarization reaches Mexican fir forests.",
            "score": 9.1,
            "keywords": [
                "monarch butterfly",
                "migration",
                "insects"
            ]
        },
        {
            "topic": "Crow and Raven Intelligence",
            "title": "Crows Recognize Human Faces and Pass Grudges to Their Children",
            "description": "Corvid tool crafting, multi-step puzzle solving, and episodic memory matching great apes.",
            "score": 9.4,
            "keywords": [
                "crow intelligence",
                "animal behavior",
                "corvids"
            ]
        },
        {
            "topic": "Glacial Landforms",
            "title": "How Millions of Tons of Moving Ice Carved Fjords and Great Lakes",
            "description": "Glacial striations, moraines, and the massive weight of continental ice sheets.",
            "score": 8.7,
            "keywords": [
                "glaciers",
                "geology",
                "ice age"
            ]
        },
        {
            "topic": "The Green Sahara History",
            "title": "6,000 Years Ago, the Sahara Was a Lush Paradise of Lakes and Forests",
            "description": "Earth's orbital precession changes triggering African Humid Periods and ancient rock art hippos.",
            "score": 9.3,
            "keywords": [
                "green sahara",
                "climatology",
                "deserts"
            ]
        },
        {
            "topic": "Carnivorous Plants",
            "title": "The Venus Flytrap: How a Plant With No Brain or Nerves Counts to 5",
            "description": "Trigger hairs activating action potentials and closing the trap in a tenth of a second.",
            "score": 9.2,
            "keywords": [
                "venus flytrap",
                "carnivorous plants",
                "botany"
            ]
        },
        {
            "topic": "Cephalopod Camouflage",
            "title": "How Octopuses Become Completely Invisible in Under 200 Milliseconds",
            "description": "Chromatophores, iridophores, and leucophores controlled directly by motor neurons in the skin.",
            "score": 9.5,
            "keywords": [
                "octopus",
                "camouflage",
                "cephalopods"
            ]
        },
        {
            "topic": "Supervolcano Eruptions",
            "title": "Yellowstone Supervolcano: What Happens When a Caldera Blows?",
            "description": "Magma chambers holding 10,000 cubic kilometers of molten rhyolite and volcanic winter risks.",
            "score": 9.3,
            "keywords": [
                "supervolcano",
                "yellowstone",
                "geology"
            ]
        },
        {
            "topic": "Amazon Flying Rivers",
            "title": "The Invisible Rivers in the Sky Pumping Trillions of Liters of Water",
            "description": "How 400 billion Amazonian trees transpire moisture creating continent-spanning cloud rivers.",
            "score": 9.1,
            "keywords": [
                "amazon rainforest",
                "flying rivers",
                "meteorology"
            ]
        },
        {
            "topic": "Arctic Permafrost Thaw",
            "title": "The Ancient Viruses and Methane Bombs Trapped Under Siberian Ice",
            "description": "Thermokarst collapse releasing 1,500 billion tons of trapped ancient organic carbon.",
            "score": 9.2,
            "keywords": [
                "permafrost",
                "arctic",
                "climate science"
            ]
        },
        {
            "topic": "Animal Echolocation",
            "title": "How Bats and Dolphins 'See' the World Through Acoustic Holograms",
            "description": "High-frequency ultrasound chirps and melon organ acoustic lenses mapping target density.",
            "score": 8.9,
            "keywords": [
                "echolocation",
                "bats",
                "bioacoustics"
            ]
        },
        {
            "topic": "Bioluminescent Fungi",
            "title": "Ghost Mushrooms: The Forests That Glow Bright Green at Night",
            "description": "Circadian-regulated fungal luciferin emitting 530nm green light to attract spore-dispersing beetles.",
            "score": 8.8,
            "keywords": [
                "bioluminescent fungi",
                "mushrooms",
                "rainforest"
            ]
        },
        {
            "topic": "Electric Eel Shock Biology",
            "title": "How an Electric Eel Generates 860 Volts of Electricity to Stun Prey",
            "description": "Electrocytes stacking thousands of ion pumps like a living biological car battery.",
            "score": 9.3,
            "keywords": [
                "electric eel",
                "bioelectricity",
                "electrocytes"
            ]
        },
        {
            "topic": "Coral Reef Symbiosis",
            "title": "The Delicate Microscopic Marriage That Builds the Largest Living Structures",
            "description": "Coral polyp symbiosis with photosynthetic zooxanthellae and the biology of coral bleaching.",
            "score": 9.0,
            "keywords": [
                "coral reefs",
                "symbiosis",
                "ocean conservation"
            ]
        },
        {
            "topic": "Predatory Bird Vision",
            "title": "How Falcons Spot a Running Field Mouse From 3 Miles in the Air",
            "description": "Dual foveas, pectin oculi, and UV cone receptors granting four times human visual acuity.",
            "score": 9.2,
            "keywords": [
                "falcon vision",
                "birds of prey",
                "animal optics"
            ]
        },
        {
            "topic": "Deepest Caves on Earth",
            "title": "Veryovkina Cave: Descending 7,200 Feet Into Earth's Darkest Abyss",
            "description": "Underground limestone karst rivers, subterranean waterfalls, and blind endemic troglobites.",
            "score": 9.1,
            "keywords": [
                "caves",
                "spelunking",
                "veryovkina"
            ]
        },
        {
            "topic": "Ant Colony Superorganisms",
            "title": "How 10 Million Leafcutter Ants Run an Industrial Farming Society",
            "description": "Division of labor, subterranean climate control, and cultivating symbiotic Leucoagaricus fungi.",
            "score": 9.3,
            "keywords": [
                "ant colony",
                "superorganism",
                "entomology"
            ]
        },
        {
            "topic": "Great Barrier Reef Biodiversity",
            "title": "The Coral Super-Structure Visible From Orbit That Houses 1,500 Fish Species",
            "description": "Mass spawning events synced to the full moon and marine trophic cascade balances.",
            "score": 8.9,
            "keywords": [
                "great barrier reef",
                "marine biology",
                "biodiversity"
            ]
        },
        {
            "topic": "Geothermal Extremophile Life",
            "title": "The Microbes Living in Boiling Sulfuric Acid Hot Springs",
            "description": "Thermus aquaticus yielding Taq polymerase that enabled the entire PCR diagnostic revolution.",
            "score": 9.2,
            "keywords": [
                "extremophiles",
                "hot springs",
                "biotech"
            ]
        },
        {
            "topic": "Hydrothermal Chemosynthesis",
            "title": "Giant Tube Worms With No Mouths Living on Toxic Volcanic Hydrogen Sulfide",
            "description": "Trophosome bacterial symbionts turning volcanic sulfur into metabolic sugar without sunlight.",
            "score": 9.0,
            "keywords": [
                "hydrothermal vents",
                "chemosynthesis",
                "deep sea"
            ]
        },
        {
            "topic": "Caterpillar Metamorphosis",
            "title": "Caterpillars Literally Melt Into Soup Inside the Chrysalis Before Becoming Butterflies",
            "description": "Enzymatic histolysis dissolving larval organs while imaginal discs assemble new wings and eyes.",
            "score": 9.5,
            "keywords": [
                "metamorphosis",
                "butterflies",
                "entomology"
            ]
        },
        {
            "topic": "Whale Song Acoustic Physics",
            "title": "Blue Whale Infrasound Traveling 1,000 Miles Through Ocean SOFAR Channels",
            "description": "Deep sound channel acoustic refraction carrying low-frequency whale vocalizations across ocean basins.",
            "score": 9.1,
            "keywords": [
                "whale songs",
                "acoustics",
                "marine mammals"
            ]
        },
        {
            "topic": "Beaver Environmental Engineers",
            "title": "How One Rodent Species Drought-Proofs Entire Continental River Systems",
            "description": "Beaver dam wetlands recharging underground water tables and creating biodiversity hotspots.",
            "score": 9.0,
            "keywords": [
                "beavers",
                "keystone species",
                "ecology"
            ]
        },
        {
            "topic": "Mangrove Coastal Shields",
            "title": "The Walking Trees That Drink Seawater and Stop Category 5 Tsunami Waves",
            "description": "Ultrafiltration root membranes desalinating seawater and interlocking stilt roots trapping sediment.",
            "score": 8.8,
            "keywords": [
                "mangroves",
                "coastal ecosystems",
                "marine botany"
            ]
        },
        {
            "topic": "Peregrine Falcon Dive Physics",
            "title": "The Animal That Hits 242 MPH: Aerodynamics of the Fastest Living Creature",
            "description": "Heart rates of 600 bpm, third eyelid membranes, and nasal tubercles guiding supersonic air vortexes.",
            "score": 9.4,
            "keywords": [
                "peregrine falcon",
                "terminal velocity",
                "bird physics"
            ]
        },
        {
            "topic": "Desert Resurrection Plants",
            "title": "The Plants That Lose 95% of Their Water and Come Back to Life in 3 Hours",
            "description": "Selaginella lepidophylla entering anhydrobiosis with protective trehalose sugar glass.",
            "score": 8.9,
            "keywords": [
                "resurrection plant",
                "anhydrobiosis",
                "botany"
            ]
        },
        {
            "topic": "Zombie Insect Fungi (Ophiocordyceps)",
            "title": "The Real 'Last of Us' Fungus That Hijacks Ant Muscles and Minds",
            "description": "Fungal hyphae controlling ant neuromuscular systems to climb high twigs before erupting spores.",
            "score": 9.6,
            "keywords": [
                "cordyceps",
                "zombie ant",
                "fungi"
            ]
        },
        {
            "topic": "Deep-Sea Gigantism",
            "title": "Why Deep-Sea Isopods and Colossal Squids Grow to Monster Sizes",
            "description": "Kleiber's law, cold temperature metabolic slowing, and hyperbaric oxygen availability.",
            "score": 9.3,
            "keywords": [
                "abyssal gigantism",
                "giant squid",
                "deep sea"
            ]
        },
        {
            "topic": "Chameleon Color Crystallography",
            "title": "Chameleons Don't Use Pigments to Change Color\u2014They Use Photonic Crystals",
            "description": "Lattice spacing of guanine nanocrystals in iridophores reflecting varying wavelengths of light.",
            "score": 9.4,
            "keywords": [
                "chameleon",
                "photonic crystals",
                "optics"
            ]
        },
        {
            "topic": "Mountain Orogeny",
            "title": "How the Himalayas Are Still Growing Half an Inch Every Single Year",
            "description": "Continental collision of the Indian and Eurasian tectonic plates lifting marine limestone to Everest's peak.",
            "score": 8.8,
            "keywords": [
                "himalayas",
                "mountain building",
                "plate tectonics"
            ]
        },
        {
            "topic": "Aurora Borealis Mechanics",
            "title": "The Solar Particle Collisions That Paint the Polar Skies Neon Green",
            "description": "Solar wind protons exciting oxygen atoms at 100km altitude to emit 557.7nm photon waves.",
            "score": 9.2,
            "keywords": [
                "aurora borealis",
                "northern lights",
                "geomagnetism"
            ]
        },
        {
            "topic": "Trophic Cascades in Yellowstone",
            "title": "How Reintroducing 14 Wolves Changed the Physical Course of Rivers",
            "description": "Apex predators curbing deer overbrowsing, reviving willows, stabilizing riverbanks, and returning songbirds.",
            "score": 9.5,
            "keywords": [
                "trophic cascade",
                "wolves yellowstone",
                "ecology"
            ]
        },
        {
            "topic": "Puerto Rico Bioluminescent Bays",
            "title": "Mosquito Bay: The Densest Glowing Water Ecosystem in the World",
            "description": "Over 700,000 Pyrodinium bahamense dinoflagellates per gallon of water in mangrove lagoons.",
            "score": 8.9,
            "keywords": [
                "bioluminescent bay",
                "mosquito bay",
                "marine ecology"
            ]
        },
        {
            "topic": "Honeybee Waggle Dance",
            "title": "The Symbolic Language Bees Use to Calculate Distance and Vector Angle",
            "description": "Karl von Frisch's Nobel Prize discovery on how scout bees communicate flower locations relative to the sun.",
            "score": 9.2,
            "keywords": [
                "waggle dance",
                "bee communication",
                "animal behavior"
            ]
        },
        {
            "topic": "Desert Oasis Hydrology",
            "title": "How Deep Fossil Aquifers Push Fresh Water Up Through Scorching Sand",
            "description": "Artesian pressure forcing 10,000-year-old rainwater reserves up through geological fault fractures.",
            "score": 8.8,
            "keywords": [
                "desert oasis",
                "hydrology",
                "groundwater"
            ]
        },
        {
            "topic": "Abyssal Siphonophores",
            "title": "The 150-Foot Deep-Sea Creature That is Actually Millions of Clones",
            "description": "Colonial hydrozoan zooids specializing into feeding, swimming, and reproductive organs.",
            "score": 9.3,
            "keywords": [
                "siphonophore",
                "colonial organisms",
                "ocean mystery"
            ]
        },
        {
            "topic": "Platypus Evolutionary Paradox",
            "title": "The Duck-Billed Mammal That Lays Eggs, Sweats Milk, and Has Toxic Spurs",
            "description": "Electroreceptive bills, cloacas, and 10 sex chromosomes making the platypus an evolutionary marvel.",
            "score": 9.4,
            "keywords": [
                "platypus",
                "evolution",
                "mammals"
            ]
        },
        {
            "topic": "Giant Sequoia Fire Ecology",
            "title": "Why 3,000-Year-Old Redwood Trees Literally Need Wildfires to Reproduce",
            "description": "Serotinous cones opening only in intense convective heat and mineral-rich ash nursery beds.",
            "score": 9.0,
            "keywords": [
                "giant sequoia",
                "pyro-ecology",
                "forestry"
            ]
        },
        {
            "topic": "Slime Mold Maze Solving",
            "title": "The Brainless Single-Celled Blob That Can Solve Complex Tokyo Subway Mazes",
            "description": "Physarum polycephalum pulsating protoplasmic cytoplasmic flow to optimize nutrient networks.",
            "score": 9.5,
            "keywords": [
                "slime mold",
                "biological computing",
                "unicellular"
            ]
        },
        {
            "topic": "Avian Magnetoreception",
            "title": "How Migrating Birds Literally 'See' Earth's Magnetic Field With Quantum Entanglement",
            "description": "Cryptochrome-4 eye proteins undergoing radical-pair quantum spin state transitions.",
            "score": 9.4,
            "keywords": [
                "magnetoreception",
                "quantum biology",
                "bird migration"
            ]
        },
        {
            "topic": "Venus Flytrap Action Potentials",
            "title": "The Electrical Voltages Plants Send Without Having a Central Nervous System",
            "description": "Calcium-activated mechanosensitive ion channels tripping electrical snap-buckling leaf geometry.",
            "score": 9.1,
            "keywords": [
                "plant electrophysiology",
                "action potentials",
                "botany"
            ]
        },
        {
            "topic": "Cuttlefish Polarization Vision",
            "title": "The Animals That Communicate Using Secret Light Angles Invisible to Humans",
            "description": "W-shaped pupils and photoreceptors detecting linear polarization angles underwater.",
            "score": 9.0,
            "keywords": [
                "cuttlefish",
                "polarization vision",
                "marine optics"
            ]
        },
        {
            "topic": "Arctic Fox Winter Physiology",
            "title": "How Arctic Foxes Sleep Comfortably at Minus 50 Degrees Celsius",
            "description": "Counter-current heat exchange in foot pads and fur with the highest thermal insulation of any mammal.",
            "score": 8.9,
            "keywords": [
                "arctic fox",
                "thermoregulation",
                "winter biology"
            ]
        },
        {
            "topic": "Subterranean Troglobites",
            "title": "Blind White Cave Fish That Lost Their Eyes to Save 15% of Their Energy",
            "description": "Regressive evolution in dark subterranean biomes optimizing energy conservation.",
            "score": 8.8,
            "keywords": [
                "troglobites",
                "cave fish",
                "regressive evolution"
            ]
        },
        {
            "topic": "Cleaner Fish Symbiotic Stations",
            "title": "Why Deadly Apex Sharks Wait Patiently in Line at Coral Reef Spas",
            "description": "Labroides dimidiatus eating parasites off moray eels and pelagic manta rays without being eaten.",
            "score": 9.1,
            "keywords": [
                "cleaner fish",
                "mutualism",
                "coral reef"
            ]
        },
        {
            "topic": "Spider Silk Tensile Physics",
            "title": "Five Times Stronger Than Kevlar: The Polymer Engineering of Spider Silk",
            "description": "Beta-sheet alanine crystals embedded in glycine-rich amorphous protein matrices.",
            "score": 9.3,
            "keywords": [
                "spider silk",
                "biopolymers",
                "materials physics"
            ]
        },
        {
            "topic": "Geyser Hydrodynamics",
            "title": "The Underground Plumbing That Makes Old Faithful Erupt Like Clockwork",
            "description": "Superheated water traps flashing to steam when pressure drops in narrow subterranean silica conduits.",
            "score": 8.9,
            "keywords": [
                "geysers",
                "old faithful",
                "hydrothermal"
            ]
        },
        {
            "topic": "Pitcher Plant Slip Chemistry",
            "title": "The Super-Hydrophilic Rim That Makes Ants Aquaplane Into Acid Vats",
            "description": "Wax crystal microstructure and water-film lubricity inside Nepenthes pitcher traps.",
            "score": 9.0,
            "keywords": [
                "pitcher plants",
                "nepenthes",
                "surface physics"
            ]
        },
        {
            "topic": "Bowerbird Architectural Courtship",
            "title": "The Birds That Paint and Curate Blue Modern Art Galleries to Win a Mate",
            "description": "Vocal mimicry, forced perspective illusions, and geometric symmetry in satin bowerbird nests.",
            "score": 9.2,
            "keywords": [
                "bowerbird",
                "sexual selection",
                "animal architecture"
            ]
        }
    ],
    "psychology": [
        {
            "topic": "Cognitive Biases in Daily Life",
            "title": "The Invisible Mental Shortcuts That Hijack Almost Every Decision You Make",
            "description": "System 1 vs System 2 thinking (Daniel Kahneman) and how heuristic shortcuts lead to systematic errors.",
            "score": 9.3,
            "keywords": [
                "cognitive biases",
                "kahneman",
                "decision making"
            ]
        },
        {
            "topic": "The Bystander Effect",
            "title": "The Tragic Psychology of Why Crowds Freeze During Emergencies",
            "description": "Diffusion of responsibility and pluralistic ignorance in public crises.",
            "score": 9.2,
            "keywords": [
                "bystander effect",
                "social psychology",
                "human behavior"
            ]
        },
        {
            "topic": "Placebo and Nocebo Effects",
            "title": "How Sugar Pills Can Cure Pain and Fake Side Effects Can Cause Sickness",
            "description": "The neurochemistry of expectation releasing real endogenous endorphins in the brain.",
            "score": 9.4,
            "keywords": [
                "placebo effect",
                "nocebo",
                "mind body connection"
            ]
        },
        {
            "topic": "Flow State Neuroscience",
            "title": "Mihaly Csikszentmihalyi: How to Enter the Ultimate State of Effortless Mastery",
            "description": "Transient hypofrontality, dopamine-norepinephrine cascades, and the challenge-skill balance.",
            "score": 9.5,
            "keywords": [
                "flow state",
                "peak performance",
                "neuroscience"
            ]
        },
        {
            "topic": "Confirmation Bias & Echo Chambers",
            "title": "Why Smart People Reject Facts That Disagree With Their Beliefs",
            "description": "Motivated reasoning and cognitive dissonance protection in political and personal beliefs.",
            "score": 9.1,
            "keywords": [
                "confirmation bias",
                "echo chamber",
                "reasoning"
            ]
        },
        {
            "topic": "Stanford Prison Experiment Deconstruction",
            "title": "The Scandalous Flaws in the Most Famous Social Psychology Experiment",
            "description": "Demand characteristics, experimenter coaching, and why Zimbardo's conclusions fell apart.",
            "score": 9.3,
            "keywords": [
                "stanford prison experiment",
                "zimbardo",
                "psychology history"
            ]
        },
        {
            "topic": "The Priming Effect",
            "title": "How Subtle Words and Smells Subconsciously Dictate What You Buy",
            "description": "Associative semantic networks activating related concepts without conscious awareness.",
            "score": 8.9,
            "keywords": [
                "priming effect",
                "subconscious",
                "behavioral economics"
            ]
        },
        {
            "topic": "The Halo Effect",
            "title": "Why Attractive People Are Automatically Assumed to Be Smarter and Kinder",
            "description": "Edward Thorndike's cognitive bias where one positive trait spills over to unrelated judgments.",
            "score": 9.0,
            "keywords": [
                "halo effect",
                "social perception",
                "attraction"
            ]
        },
        {
            "topic": "The Sunk Cost Fallacy",
            "title": "Why You Finish Terrible Movies and Stay in Toxic Relationships",
            "description": "Honoring sunk past investments rather than evaluating marginal future utility.",
            "score": 9.2,
            "keywords": [
                "sunk cost",
                "decision traps",
                "behavioral science"
            ]
        },
        {
            "topic": "Imposter Syndrome Psychology",
            "title": "Why the Most Qualified People Feel Like Complete Frauds",
            "description": "Pluralistic ignorance of internal struggle vs visible external competence in high achievers.",
            "score": 9.4,
            "keywords": [
                "imposter syndrome",
                "self doubt",
                "psychology"
            ]
        },
        {
            "topic": "Anchoring Bias in Negotiations",
            "title": "Why the First Number Said Controls the Entire Salary Negotiation",
            "description": "Cognitive anchor points anchoring estimates and adjusting insufficiently from the initial reference.",
            "score": 9.1,
            "keywords": [
                "anchoring bias",
                "negotiation",
                "pricing psychology"
            ]
        },
        {
            "topic": "The Dunning-Kruger Effect",
            "title": "The Scientific Reason Why Incompetent People Are the Most Confident",
            "description": "Metacognitive deficits preventing beginners from recognizing their own lack of expertise.",
            "score": 9.5,
            "keywords": [
                "dunning kruger",
                "confidence",
                "expertise"
            ]
        },
        {
            "topic": "Cognitive Dissonance Resolution",
            "title": "Leon Festinger: What Happens When Your Actions Clash With Your Morals",
            "description": "Rationalization, belief adjustment, and the discomfort of holding contradictory cognitions.",
            "score": 9.0,
            "keywords": [
                "cognitive dissonance",
                "festinger",
                "rationalization"
            ]
        },
        {
            "topic": "Dunbar's Number (150)",
            "title": "Why the Human Brain Cannot Handle More Than 150 Social Relationships",
            "description": "Neocortex volume ratios limiting the number of stable social connections in primates.",
            "score": 9.1,
            "keywords": [
                "dunbars number",
                "social groups",
                "evolutionary psychology"
            ]
        },
        {
            "topic": "Serial Position Effect",
            "title": "Primacy and Recency: Why You Only Remember the Beginning and the End",
            "description": "Working memory dump and long-term storage transfer explaining interview and presentation recall.",
            "score": 8.8,
            "keywords": [
                "serial position",
                "memory recall",
                "primacy effect"
            ]
        },
        {
            "topic": "Evolutionary Negativity Bias",
            "title": "Why Bad News Sticks in Your Brain 5 Times Harder Than Good News",
            "description": "Evolutionary survival value: mistaking a rope for a snake is safe, mistaking a snake for a rope is fatal.",
            "score": 9.3,
            "keywords": [
                "negativity bias",
                "evolutionary psychology",
                "anxiety"
            ]
        },
        {
            "topic": "Illusion of Explanatory Depth",
            "title": "You Think You Know How a Zipper Works (Until You Try to Draw It)",
            "description": "Mistaking familiarity for functional understanding in mechanical and social concepts.",
            "score": 9.2,
            "keywords": [
                "explanatory depth",
                "knowledge illusions",
                "metacognition"
            ]
        },
        {
            "topic": "Fundamental Attribution Error",
            "title": "Why You Judge Yourself by Your Intentions, but Others by Their Mistakes",
            "description": "Overemphasizing personal disposition while ignoring situational context in other people.",
            "score": 9.0,
            "keywords": [
                "attribution error",
                "social psychology",
                "empathy"
            ]
        },
        {
            "topic": "The Marshmallow Test Revisited",
            "title": "Delay of Gratification: Did Self-Control Predict Life Success?",
            "description": "How socioeconomic background and institutional trust reinterpreted Walter Mischel's famous study.",
            "score": 9.1,
            "keywords": [
                "marshmallow test",
                "delayed gratification",
                "child psychology"
            ]
        },
        {
            "topic": "The Paradox of Choice",
            "title": "Barry Schwartz: Why Having 30 Kinds of Jam Makes You Buy Nothing",
            "description": "Choice overload, decision paralysis, and anticipated post-purchase regret.",
            "score": 9.2,
            "keywords": [
                "paradox of choice",
                "decision paralysis",
                "consumer psychology"
            ]
        },
        {
            "topic": "The Hedonic Treadmill",
            "title": "Why Winning the Lottery Won't Make You Happier After 12 Months",
            "description": "Hedonic adaptation returning humans to a baseline emotional setpoint regardless of events.",
            "score": 9.4,
            "keywords": [
                "hedonic treadmill",
                "happiness",
                "adaptation"
            ]
        },
        {
            "topic": "The Pygmalion & Golem Effects",
            "title": "How Others' High or Low Expectations Silently Mold Your Performance",
            "description": "Subconscious non-verbal cues and behavioral confirmation turning expectations into reality.",
            "score": 8.9,
            "keywords": [
                "pygmalion effect",
                "expectations",
                "management"
            ]
        },
        {
            "topic": "Loss Aversion Asymmetry",
            "title": "Why Losing $100 Hurts Twice as Much as Winning $100 Feels Good",
            "description": "Prospect theory (Kahneman & Tversky) and the asymmetric curvature of utility functions.",
            "score": 9.2,
            "keywords": [
                "loss aversion",
                "prospect theory",
                "behavioral finance"
            ]
        },
        {
            "topic": "The Spotlight Effect",
            "title": "The Psychology of Feeling Like Everyone is Staring at Your Flaws",
            "description": "Egocentric anchoring causing humans to overestimate how much others notice their appearance.",
            "score": 9.3,
            "keywords": [
                "spotlight effect",
                "social anxiety",
                "egocentrism"
            ]
        },
        {
            "topic": "Pareidolia and Patternicity",
            "title": "Why Your Brain Sees Human Faces in Clouds, Toasters, and Mars Rocks",
            "description": "The fusiform face area (FFA) in the brain running hyper-sensitive facial detection algorithms.",
            "score": 9.0,
            "keywords": [
                "pareidolia",
                "fusiform face",
                "pattern recognition"
            ]
        },
        {
            "topic": "The Self-Serving Bias",
            "title": "Wins Are My Talent, Losses Are Bad Luck: The Ego's Defense Shield",
            "description": "Attributing positive outcomes to internal skill and negative outcomes to external factors.",
            "score": 8.8,
            "keywords": [
                "self serving bias",
                "ego defense",
                "attribution"
            ]
        },
        {
            "topic": "Hindsight Bias ('I Knew It All Along')",
            "title": "Why Past Events Always Seem Inevitable After They Happen",
            "description": "Memory reconstruction adjusting past probability estimates to match current known outcomes.",
            "score": 8.9,
            "keywords": [
                "hindsight bias",
                "memory",
                "forecasting"
            ]
        },
        {
            "topic": "The Illusory Truth Effect",
            "title": "Repeat a Lie Often Enough and People Will Believe It's True",
            "description": "Cognitive fluency: familiar statements process with less mental friction and are mistaken for truth.",
            "score": 9.5,
            "keywords": [
                "illusory truth",
                "propaganda",
                "cognitive fluency"
            ]
        },
        {
            "topic": "The Zeigarnik Effect",
            "title": "Why Unfinished Tasks Haunt Your Brain Until You Complete Them",
            "description": "Bluma Zeigarnik's discovery on how incomplete goals maintain cognitive tension in working memory.",
            "score": 9.3,
            "keywords": [
                "zeigarnik effect",
                "productivity",
                "open loops"
            ]
        },
        {
            "topic": "Social Proof in Herd Behavior",
            "title": "The Elevator Experiment: Why People Turn Backwards Just Because Others Do",
            "description": "Informational social influence and looking to the group to resolve ambiguity.",
            "score": 9.1,
            "keywords": [
                "social proof",
                "conformity",
                "asch experiment"
            ]
        },
        {
            "topic": "Foot-in-the-Door Technique",
            "title": "How Small Agreemets Lead to Massive Compliances",
            "description": "Self-perception theory: agreeing to a tiny initial request shifts identity toward compliance.",
            "score": 8.9,
            "keywords": [
                "foot in the door",
                "persuasion",
                "sales psychology"
            ]
        },
        {
            "topic": "The Framing Effect",
            "title": "'90% Fat-Free' vs '10% Fat': How Wording Alters Choices Completely",
            "description": "Decision shifts driven by presenting identical objective probabilities as positive gains vs negative losses.",
            "score": 9.2,
            "keywords": [
                "framing effect",
                "marketing psychology",
                "heuristics"
            ]
        },
        {
            "topic": "Optimism Bias & Risk Perception",
            "title": "The Evolutionary Reason Why Everyone Thinks Bad Things Only Happen to Others",
            "description": "Tali Sharot's research on frontal cortex suppression of negative future estimations.",
            "score": 9.0,
            "keywords": [
                "optimism bias",
                "risk perception",
                "neuroscience"
            ]
        },
        {
            "topic": "The Invisible Gorilla (Selective Attention)",
            "title": "How You Can Look Directly at an 800-Pound Gorilla and Not See It",
            "description": "Inattentional blindness and how focused mental attention filters out glaring background anomalies.",
            "score": 9.4,
            "keywords": [
                "invisible gorilla",
                "selective attention",
                "chabris"
            ]
        },
        {
            "topic": "The Mere Exposure Effect",
            "title": "Why You Hate Photos of Yourself but Love Seeing Yourself in Mirrors",
            "description": "Robert Zajonc: repeated perceptual exposure breeding subconscious positive preference.",
            "score": 9.1,
            "keywords": [
                "mere exposure",
                "familiarity",
                "psychology"
            ]
        },
        {
            "topic": "False Memory Syndrome",
            "title": "Elizabeth Loftus: How to Plant a Childhood Memory That Never Happened",
            "description": "Memory suggestibility, leading questions, and the fragility of eyewitness testimony.",
            "score": 9.4,
            "keywords": [
                "false memory",
                "elizabeth loftus",
                "eyewitness"
            ]
        },
        {
            "topic": "Bystander Apathy Origins",
            "title": "Kitty Genovese: Deconstructing the Myth That Started Modern Social Psychology",
            "description": "Investigative historical records correcting the sensationalized narrative of 38 silent witnesses.",
            "score": 9.0,
            "keywords": [
                "kitty genovese",
                "bystander apathy",
                "urban legends"
            ]
        },
        {
            "topic": "Emotional Contagion in Crowds",
            "title": "How Smiling or Panicking Spreads Like a Psychological Virus",
            "description": "Micro-expressions, autonomic vocal tone matching, and synchronized group physiology.",
            "score": 8.9,
            "keywords": [
                "emotional contagion",
                "group dynamics",
                "mirroring"
            ]
        },
        {
            "topic": "The Gambler's Fallacy",
            "title": "Why 10 Red Roulette Spins Does NOT Make Black More Likely",
            "description": "Treating independent statistical events as if they possess self-correcting memory.",
            "score": 9.1,
            "keywords": [
                "gamblers fallacy",
                "probability",
                "casino math"
            ]
        },
        {
            "topic": "The Default Effect in Organ Donation",
            "title": "How a Single Pre-Checked Box Saves Thousands of Lives",
            "description": "Status quo bias and the power of opt-in vs opt-out policy architecture.",
            "score": 9.2,
            "keywords": [
                "default effect",
                "nudge theory",
                "behavioral design"
            ]
        },
        {
            "topic": "Moral Licensing Effect",
            "title": "Why Ordering a Diet Salad Makes You Eat a Double-Chocolate Cake Later",
            "description": "Subconscious moral bookkeeping: positive ethical actions licensing subsequent selfish or unhealthy choices.",
            "score": 9.0,
            "keywords": [
                "moral licensing",
                "self regulation",
                "behavior"
            ]
        },
        {
            "topic": "Framing of Risk in Game Theory",
            "title": "The Prisoner's Dilemma: Why Rational Individuals Produce Disastrous Group Outcomes",
            "description": "Nash equilibrium, tit-for-tat strategies, and the evolution of mutual cooperation.",
            "score": 9.2,
            "keywords": [
                "prisoners dilemma",
                "game theory",
                "cooperation"
            ]
        },
        {
            "topic": "Mirror Neurons and Empathy",
            "title": "The Brain Cells That Fire When You Watch Someone Else Stub Their Toe",
            "description": "Giacomo Rizzolatti's discovery of premotor neurons mapping observed actions onto personal sensation.",
            "score": 9.3,
            "keywords": [
                "mirror neurons",
                "empathy",
                "neurobiology"
            ]
        },
        {
            "topic": "The Peak-End Rule",
            "title": "Why Colonoscopies and Vacations Are Remembered Only by Two Moments",
            "description": "Daniel Kahneman: memory averaging the peak emotional intensity and the ending feeling.",
            "score": 9.3,
            "keywords": [
                "peak end rule",
                "kahneman",
                "memory evaluation"
            ]
        },
        {
            "topic": "Cognitive Behavioral Therapy (CBT)",
            "title": "Aaron Beck: How Identifying Cognitive Distortions Rewires Depressive Thought Loops",
            "description": "Catastrophizing, mind reading, and black-and-white thinking replaced by empirical evidence tests.",
            "score": 9.1,
            "keywords": [
                "cbt",
                "mental health",
                "cognitive therapy"
            ]
        },
        {
            "topic": "Schema Theory of the Mind",
            "title": "How Mental Blueprints Allow You to Navigate Restaurants You've Never Visited",
            "description": "Jean Piaget and Frederic Bartlett: cognitive frameworks organizing categorical knowledge.",
            "score": 8.8,
            "keywords": [
                "schema theory",
                "piaget",
                "cognition"
            ]
        },
        {
            "topic": "Sleep Deprivation and Emotional Volatility",
            "title": "What 24 Hours Without Sleep Does to Your Amygdala-Prefrontal Circuitry",
            "description": "Loss of top-down prefrontal inhibition causing 60% heightened emotional reactivity.",
            "score": 9.3,
            "keywords": [
                "sleep deprivation",
                "amygdala",
                "emotional health"
            ]
        },
        {
            "topic": "Emotional Intelligence (EQ)",
            "title": "Why Self-Awareness Predicts Leadership Better Than IQ",
            "description": "Daniel Goleman's 5 pillars: self-awareness, self-regulation, motivation, empathy, and social skill.",
            "score": 8.9,
            "keywords": [
                "emotional intelligence",
                "eq",
                "leadership"
            ]
        },
        {
            "topic": "The Dark Triad Personality Traits",
            "title": "Machiavellianism, Narcissism, and Psychopathy: Spotting Predators",
            "description": "Subclinical manipulative personality archetypes and evolutionary exploitation strategies.",
            "score": 9.5,
            "keywords": [
                "dark triad",
                "narcissism",
                "psychopathy"
            ]
        },
        {
            "topic": "Social Identity Theory",
            "title": "Henri Tajfel: How Random Coin Flips Create Fierce Us vs Them Tribalism",
            "description": "Minimal group paradigm experiments showing how arbitrarily formed teams immediately discriminate.",
            "score": 9.2,
            "keywords": [
                "social identity",
                "tribalism",
                "in group bias"
            ]
        },
        {
            "topic": "Terror Management Theory",
            "title": "How the Unconscious Fear of Death Drives Culture, Patriotism, and Fame",
            "description": "Cultural worldviews and self-esteem acting as psychological buffers against existential mortality panic.",
            "score": 9.1,
            "keywords": [
                "terror management",
                "existential psychology",
                "culture"
            ]
        },
        {
            "topic": "Learned Helplessness",
            "title": "Martin Seligman: Why Repeated Failure Convinces Animals to Stop Trying",
            "description": "Conditioned passivity under inescapable stress and the foundation of positive psychology.",
            "score": 9.2,
            "keywords": [
                "learned helplessness",
                "seligman",
                "resilience"
            ]
        }
    ],
    "health": [
        {
            "topic": "Circadian Rhythm Biology",
            "title": "How Morning Sunlight Sets Your Master Circadian Clock for Peak Energy",
            "description": "Intrinsically photosensitive retinal ganglion cells (ipRGCs) resetting the suprachiasmatic nucleus (SCN).",
            "score": 9.4,
            "keywords": [
                "circadian rhythm",
                "sleep science",
                "sunlight"
            ]
        },
        {
            "topic": "The Gut-Brain Axis",
            "title": "How 38 Trillion Gut Bacteria Manufacture 90% of Your Body's Serotonin",
            "description": "The vagus nerve highway linking microbiome bacterial metabolites to mood, anxiety, and depression.",
            "score": 9.5,
            "keywords": [
                "gut brain axis",
                "microbiome",
                "serotonin"
            ]
        },
        {
            "topic": "Autophagy and Fasting",
            "title": "Cellular Garbage Collection: What Happens When You Fast for 16 Hours",
            "description": "Yoshinori Ohsumi's Nobel Prize research on lysosomes clearing out damaged proteins and mitochondria.",
            "score": 9.3,
            "keywords": [
                "autophagy",
                "intermittent fasting",
                "longevity"
            ]
        },
        {
            "topic": "Cold Exposure & Brown Fat",
            "title": "Why Ice Baths Activate Thermogenic Brown Adipose Tissue",
            "description": "UCP1 mitochondrial uncoupling burning visceral white fat to generate internal core body heat.",
            "score": 9.1,
            "keywords": [
                "cold plunge",
                "brown fat",
                "metabolism"
            ]
        },
        {
            "topic": "Zone 2 Cardio Physiology",
            "title": "Why Slow, Easy Running is the Secret to Mitochondrial Longevity",
            "description": "Peter Attia: optimizing fat oxidation and mitochondrial lactate clearance without crossing aerobic threshold.",
            "score": 9.3,
            "keywords": [
                "zone 2 cardio",
                "mitochondria",
                "endurance"
            ]
        },
        {
            "topic": "Strength Training for Longevity",
            "title": "Why Muscle Mass is Your Metabolic Retirement Fund After Age 40",
            "description": "Sarcopenia prevention, insulin sensitivity, bone mineral density, and fall resistance in older age.",
            "score": 9.4,
            "keywords": [
                "strength training",
                "sarcopenia",
                "longevity"
            ]
        },
        {
            "topic": "Blue Zones Lifestyle Patterns",
            "title": "The Secrets of 5 Places on Earth Where People Routinely Live to 100",
            "description": "Okinawa, Sardinia, Nicoya: natural movement, plant-heavy diets, social connection, and Ikigai purpose.",
            "score": 9.2,
            "keywords": [
                "blue zones",
                "centenarians",
                "healthy aging"
            ]
        },
        {
            "topic": "Chronic Systemic Inflammation",
            "title": "The Silent Fire: How Low-Grade Inflammation Damages Arteries and Organs",
            "description": "High-sensitivity C-reactive protein (hs-CRP) and cytokine storms accelerating cardiovascular disease.",
            "score": 9.0,
            "keywords": [
                "inflammation",
                "crp",
                "preventative medicine"
            ]
        },
        {
            "topic": "Insulin Resistance Mechanics",
            "title": "Why 1 in 3 Adults Has Prediabetes and Doesn't Even Know It",
            "description": "Cellular receptor desensitization, ectopic fat accumulation in the liver, and metabolic syndrome.",
            "score": 9.3,
            "keywords": [
                "insulin resistance",
                "prediabetes",
                "metabolic health"
            ]
        },
        {
            "topic": "Vagus Nerve Activation",
            "title": "How Slow Physiological Sighs Reset Your Nervous System in 30 Seconds",
            "description": "Double inhalation followed by extended exhalation activating parasympathetic acetylcholine release.",
            "score": 9.2,
            "keywords": [
                "vagus nerve",
                "breathwork",
                "stress relief"
            ]
        },
        {
            "topic": "Hydration Beyond Plain Water",
            "title": "Why Drinking 8 Glasses of Distilled Water Leaves Your Cells Dehydrated",
            "description": "Sodium-potassium cellular pumps, osmolality, and the critical role of electrolytes in fluid retention.",
            "score": 8.9,
            "keywords": [
                "hydration",
                "electrolytes",
                "cellular biology"
            ]
        },
        {
            "topic": "Vitamin D Endocrine Function",
            "title": "Why Vitamin D is Actually a Steroid Hormone Controlling 1,000 Genes",
            "description": "Nuclear receptor binding, immune t-cell modulation, and seasonal mood regulation.",
            "score": 9.2,
            "keywords": [
                "vitamin d",
                "immune system",
                "hormones"
            ]
        },
        {
            "topic": "Heat Shock Proteins & Sauna",
            "title": "The Finnish Sauna Study: Why 4 Sessions a Week Slashes Heart Attack Risk by 50%",
            "description": "Cardiovascular conditioning, hyperthermic conditioning, and chaperone heat shock protein repair.",
            "score": 9.3,
            "keywords": [
                "sauna",
                "heat shock proteins",
                "cardiovascular"
            ]
        },
        {
            "topic": "HIIT Training Science",
            "title": "4 Minutes of Tabata: How Maximum Intensity Triggers Aerobic Adaptations",
            "description": "EPOC (excess post-exercise oxygen consumption) and rapid PGC-1alpha mitochondrial biogenesis.",
            "score": 9.0,
            "keywords": [
                "hiit",
                "tabata",
                "cardio"
            ]
        },
        {
            "topic": "Magnesium Types Guide",
            "title": "Magnesium Glycinate vs Citrate vs L-Threonate: What Each Form Actually Does",
            "description": "Chelation bioavailability, crossing the blood-brain barrier for sleep, and muscle cramp prevention.",
            "score": 9.2,
            "keywords": [
                "magnesium",
                "supplements",
                "nutrition science"
            ]
        },
        {
            "topic": "Adult Neuroplasticity",
            "title": "Can You Grow New Brain Cells After Age 30? The Neurogenesis Debate",
            "description": "Hippocampal dentate gyrus neurogenesis stimulated by exercise, learning, and environmental enrichment.",
            "score": 9.1,
            "keywords": [
                "neuroplasticity",
                "neurogenesis",
                "brain health"
            ]
        },
        {
            "topic": "Dopamine Baseline Management",
            "title": "Why Scrolling Social Media Ruins Your Motivation for Hard Work",
            "description": "Andrew Huberman: dopamine peaks leading to proportional troughs below baseline and pleasure-pain balance.",
            "score": 9.5,
            "keywords": [
                "dopamine",
                "motivation",
                "neurotransmitters"
            ]
        },
        {
            "topic": "Posture Biomechanics",
            "title": "Tech Neck: How Tilting Your Head Forward Adds 60 Pounds of Strain to Your Spine",
            "description": "Cervical spine lever mechanics, upper crossed syndrome, and corrective thoracic mobilization.",
            "score": 8.9,
            "keywords": [
                "posture",
                "tech neck",
                "ergonomics"
            ]
        },
        {
            "topic": "Cortisol & Stress Physiology",
            "title": "The HPA Axis: What Constant Work Stress Does to Your Blood Vessels",
            "description": "Hypothalamic-pituitary-adrenal activation, chronic vasoconstriction, and visceral fat storage.",
            "score": 9.1,
            "keywords": [
                "cortisol",
                "stress",
                "hpa axis"
            ]
        },
        {
            "topic": "Eye Health and 20-20-20 Rule",
            "title": "Digital Eye Strain: Why Your Ciliary Muscles Spasm Looking at Screens",
            "description": "Computer vision syndrome, blink rate drops from 15 to 5 per minute, and distant focus breaks.",
            "score": 8.8,
            "keywords": [
                "eye health",
                "screen time",
                "optometry"
            ]
        },
        {
            "topic": "Bone Mineral Density",
            "title": "Wolff's Law: How Impact and Heavy Lifting Make Bones Denser Than Concrete",
            "description": "Piezoelectric charges in osteocytes stimulating osteoblasts to lay down calcium hydroxyapatite.",
            "score": 9.0,
            "keywords": [
                "bone density",
                "osteoporosis",
                "wolffs law"
            ]
        },
        {
            "topic": "Heart Rate Variability (HRV)",
            "title": "The Single Metric Athletes Use to Predict Illness 48 Hours Before Symptoms",
            "description": "Millisecond variations between heartbeats as an indicator of autonomic nervous system balance.",
            "score": 9.3,
            "keywords": [
                "hrv",
                "heart rate variability",
                "recovery"
            ]
        },
        {
            "topic": "Dietary Fiber and SCFAs",
            "title": "What Happens to Gut Microbes When You Starve Them of Soluble Fiber",
            "description": "Bacterial fermentation of prebiotics into butyrate, acetate, and propionate to seal the gut barrier.",
            "score": 9.0,
            "keywords": [
                "fiber",
                "scfas",
                "gut microbiome"
            ]
        },
        {
            "topic": "VO2 Max as Longevity Marker",
            "title": "Why Your VO2 Max is the Single Strongest Predictor of How Long You Will Live",
            "description": "Cardiorespiratory fitness rankings and the steep mortality risk curve between low and elite fitness.",
            "score": 9.4,
            "keywords": [
                "vo2 max",
                "cardiorespiratory",
                "longevity"
            ]
        },
        {
            "topic": "Sugar and Glycation End-Products",
            "title": "Advanced Glycation: How Excess Blood Glucose Caramelizes Human Arteries",
            "description": "The Maillard reaction inside the body stiffening collagen fibers and aging skin and kidneys.",
            "score": 9.1,
            "keywords": [
                "glycation",
                "sugar",
                "anti-aging"
            ]
        },
        {
            "topic": "Antioxidant Mechanisms",
            "title": "Free Radicals vs Antioxidants: Why Mega-Dosing Pills Can Backfire",
            "description": "Reactive oxygen species as vital hormonal signaling molecules and the hormesis response curve.",
            "score": 8.9,
            "keywords": [
                "antioxidants",
                "free radicals",
                "hormesis"
            ]
        },
        {
            "topic": "Sleep Hygiene Protocols",
            "title": "The 10-3-2-1-0 Sleep Rule for Perfect Deep REM Cycles",
            "description": "Caffeine cutoffs, meal timing, work cessation, screen bans, and alarm discipline.",
            "score": 9.3,
            "keywords": [
                "sleep hygiene",
                "rem sleep",
                "insomnia"
            ]
        },
        {
            "topic": "Joint Cartilage Preservation",
            "title": "How Synovial Fluid Nourishes Avascular Knee Joints Through Movement",
            "description": "Why bed rest destroys joints while progressive loading stimulates chondrocyte proteoglycan repair.",
            "score": 9.0,
            "keywords": [
                "joint health",
                "cartilage",
                "movement"
            ]
        },
        {
            "topic": "Immune Memory & T-Cells",
            "title": "How Your Body Remembers and Kills Pathogens It Met 20 Years Ago",
            "description": "Memory B-cells, antigen presentation, and cytotoxic CD8+ T-cells patrolling the bloodstream.",
            "score": 9.2,
            "keywords": [
                "immunology",
                "t cells",
                "antibodies"
            ]
        },
        {
            "topic": "Caffeine Half-Life Kinetics",
            "title": "Why That 3 PM Espresso is Still Blocking Adenosine Receptors at Midnight",
            "description": "Cytochrome P450 1A2 liver enzyme breakdown and the 5-7 hour half-life curve.",
            "score": 9.4,
            "keywords": [
                "caffeine",
                "adenosine",
                "sleep disruption"
            ]
        },
        {
            "topic": "Metabolic Flexibility",
            "title": "Can Your Body Switch Effortlessly Between Burning Carbs and Burning Fat?",
            "description": "Respiratory quotient measurements and overcoming metabolic stiffness caused by constant snacking.",
            "score": 9.1,
            "keywords": [
                "metabolic flexibility",
                "fat burning",
                "nutrition"
            ]
        },
        {
            "topic": "Omega-3 EPA vs DHA",
            "title": "Why the Ratio of Omega-6 to Omega-3 in Modern Diets Causes Inflammation",
            "description": "Cell membrane phospholipid fluidity and the competitive pathway for anti-inflammatory resolvins.",
            "score": 9.0,
            "keywords": [
                "omega 3",
                "epa dha",
                "fish oil"
            ]
        },
        {
            "topic": "Sitting Disease & Endothelial Health",
            "title": "Why 8 Hours in an Office Chair Damages Blood Vessels Even if You Work Out",
            "description": "Loss of shear stress along endothelial walls reducing nitric oxide production in leg arteries.",
            "score": 9.2,
            "keywords": [
                "sitting disease",
                "sedentary",
                "endothelium"
            ]
        },
        {
            "topic": "Nature Walking & Forest Bathing",
            "title": "Shinrin-Yoku: How Inhaling Tree Phytoncides Boosts Natural Killer Cells by 50%",
            "description": "Japanese medical studies on aerosolized forest terpenes lowering cortisol and boosting immunity.",
            "score": 9.1,
            "keywords": [
                "forest bathing",
                "nature therapy",
                "phytoncides"
            ]
        },
        {
            "topic": "Oral Microbiome & Systemic Health",
            "title": "How Gum Disease Bacteria Travel to the Brain and Heart Valves",
            "description": "Porphyromonas gingivalis detected in Alzheimer's amyloid plaques and arterial atheromas.",
            "score": 9.3,
            "keywords": [
                "oral microbiome",
                "gum health",
                "systemic disease"
            ]
        },
        {
            "topic": "Strength Training and Tendon Health",
            "title": "Why Tendons Take 10 Times Longer to Adapt Than Muscle Fibers",
            "description": "Collagen remodeling turnover times and the critical need for isometric loading protocols.",
            "score": 8.9,
            "keywords": [
                "tendon health",
                "biomechanics",
                "physical therapy"
            ]
        },
        {
            "topic": "Rest Days and Supercompensation",
            "title": "You Don't Grow in the Gym: The Science of Muscle Protein Synthesis",
            "description": "The supercompensation curve: balancing micro-tears with adequate rest and amino acid timing.",
            "score": 9.0,
            "keywords": [
                "supercompensation",
                "recovery",
                "muscle growth"
            ]
        },
        {
            "topic": "Deep Sleep vs REM Sleep",
            "title": "The Dual Architecture of Sleep: Glymphatic Cleaning vs Memory Filing",
            "description": "Stage 3 slow-wave sleep brain cleansing vs REM dream emotional regulation.",
            "score": 9.4,
            "keywords": [
                "deep sleep",
                "glymphatic system",
                "rem sleep"
            ]
        },
        {
            "topic": "Noise-Induced Hearing Damage",
            "title": "Why Earbuds at 85 Decibels Destroy Stereocilia Cells That Never Regrow",
            "description": "Mechanical shear damage to hair cells in the cochlea and the permanent nature of sensorineural hearing loss.",
            "score": 8.9,
            "keywords": [
                "hearing health",
                "cochlea",
                "audiology"
            ]
        },
        {
            "topic": "Telomeres and Biological Age",
            "title": "The Cellular Shoelaces That Measure How Fast Your Body is Aging",
            "description": "Elizabeth Blackburn's Nobel Prize discovery on telomerase and how lifestyle slows telomere attrition.",
            "score": 9.3,
            "keywords": [
                "telomeres",
                "biological age",
                "longevity"
            ]
        },
        {
            "topic": "Balance & Vestibular Training",
            "title": "The Single Balance Test That Predicts 10-Year Mortality in Middle-Aged Adults",
            "description": "Semicircular canals, otolith organs, and cerebellar integration maintaining stability.",
            "score": 9.0,
            "keywords": [
                "vestibular system",
                "balance",
                "proprioception"
            ]
        },
        {
            "topic": "Micronutrient Deficiencies",
            "title": "B12, Iron, and Zinc: Subtle Warning Signs Your Body is Running on Empty",
            "description": "Macrocytic vs microcytic anemias, enzyme co-factors, and optimal serum ferritin levels.",
            "score": 8.9,
            "keywords": [
                "micronutrients",
                "anemia",
                "deficiencies"
            ]
        },
        {
            "topic": "Electrolyte Balance in Cells",
            "title": "How Sodium and Potassium Create the 70mV Voltage Across Every Cell",
            "description": "The Na+/K+-ATPase pump consuming 25% of all caloric energy burned by the human body.",
            "score": 9.1,
            "keywords": [
                "electrolytes",
                "ion pumps",
                "physiology"
            ]
        },
        {
            "topic": "The Science of Nasal Breathing",
            "title": "Why Mouth Breathing Literally Alters Your Facial Structure and Oxygen Levels",
            "description": "James Nestor's 'Breath': nitric oxide production in paranasal sinuses sterilizing and dilating airways.",
            "score": 9.5,
            "keywords": [
                "nasal breathing",
                "nitric oxide",
                "respiratory health"
            ]
        },
        {
            "topic": "Intestinal Permeability (Leaky Gut)",
            "title": "Zonulin and Tight Junctions: What Medical Science Says About Gut Permeability",
            "description": "Disruption of claudin and occludin proteins allowing bacterial lipopolysaccharides (LPS) into blood.",
            "score": 9.0,
            "keywords": [
                "leaky gut",
                "zonulin",
                "gastroenterology"
            ]
        },
        {
            "topic": "Protein Timing and Leucine Threshold",
            "title": "The Leucine Trigger: How Much Protein is Needed to Spark Muscle Synthesis?",
            "description": "mTOR activation thresholds, the muscle full effect, and distributing protein across meals.",
            "score": 9.2,
            "keywords": [
                "protein timing",
                "leucine",
                "muscle synthesis"
            ]
        },
        {
            "topic": "Grounding and Earthing Science",
            "title": "Walking Barefoot on the Earth: Fact vs Pseudoscience in Earthing Research",
            "description": "Analyzing claims of free electron absorption vs the psychological benefits of natural exposure.",
            "score": 8.8,
            "keywords": [
                "grounding",
                "earthing",
                "debunked"
            ]
        },
        {
            "topic": "Alcohol and Sleep Fragmentation",
            "title": "Why A Nightcap Drink Helps You Fall Asleep but Ruins Your Entire Night",
            "description": "GABA-ergic sedation followed by midnight glutamatergic rebound and total REM sleep suppression.",
            "score": 9.4,
            "keywords": [
                "alcohol",
                "sleep fragmentation",
                "gaba"
            ]
        },
        {
            "topic": "Peripheral Circadian Clocks",
            "title": "Your Liver Has Its Own Clock: Why Late-Night Snacking Triggers Metabolic Chaos",
            "description": "Meal timing resetting peripheral organ clocks out of phase with the central brain master clock.",
            "score": 9.2,
            "keywords": [
                "peripheral clocks",
                "chronobiology",
                "metabolism"
            ]
        },
        {
            "topic": "Daily Sunlight and Mood",
            "title": "How Bright Light Through the Eyes Triggers Hypothalamic Mood Circuits",
            "description": "Light therapy for Seasonal Affective Disorder and retinohypothalamic dopamine synthesis.",
            "score": 9.0,
            "keywords": [
                "sunlight mood",
                "sad",
                "phototherapy"
            ]
        },
        {
            "topic": "Lactate as Brain Fuel",
            "title": "Why Lactic Acid Doesn't Cause Muscle Soreness (It's Actually High-Octane Fuel)",
            "description": "George Brooks' lactate shuttle hypothesis: heart and brain preferentially burning lactate during exercise.",
            "score": 9.1,
            "keywords": [
                "lactate shuttle",
                "lactic acid myth",
                "exercise physiology"
            ]
        },
        {
            "topic": "Cold Showers and Norepinephrine",
            "title": "The 2-Minute Cold Shock That Spikes Adrenaline and Dopamine for Hours",
            "description": "Peripheral vasoconstriction and cold shock proteins boosting alertness and mental resilience.",
            "score": 9.2,
            "keywords": [
                "cold shower",
                "norepinephrine",
                "stress resilience"
            ]
        }
    ],
    "space": [
        {
            "topic": "James Webb Space Telescope",
            "title": "The 5 Unexplained Galaxies That Are Breaking Our Understanding of the Big Bang",
            "description": "How JWST detected massive fully-formed spiral galaxies merely 300 million years after the cosmic dawn.",
            "score": 9.6,
            "keywords": [
                "jwst",
                "james webb",
                "astronomy"
            ]
        },
        {
            "topic": "The Fermi Paradox Explained",
            "title": "Where Are the Aliens? The Terrifying 'Dark Forest' Solution",
            "description": "Liu Cixin's game theory explanation of cosmic civilizations staying completely silent to avoid extermination.",
            "score": 9.7,
            "keywords": [
                "fermi paradox",
                "dark forest",
                "aliens"
            ]
        },
        {
            "topic": "Event Horizon of Black Holes",
            "title": "What Happens in Your Final Seconds Falling Into a Supermassive Black Hole?",
            "description": "Gravitational time dilation, spaghettification tidal forces, and crossing the point of no return.",
            "score": 9.5,
            "keywords": [
                "black hole",
                "event horizon",
                "general relativity"
            ]
        },
        {
            "topic": "TRAPPIST-1 System",
            "title": "7 Earth-Sized Planets Orbiting One Cool Red Star 40 Light-Years Away",
            "description": "Resonant orbital chain, potential liquid water oceans, and tidal locking around an ultra-cool dwarf.",
            "score": 9.2,
            "keywords": [
                "trappist 1",
                "exoplanets",
                "astrobiology"
            ]
        },
        {
            "topic": "Europa's Hidden Ocean",
            "title": "Why NASA is Searching for Life 15 Miles Beneath Jupiter's Moon Europa",
            "description": "Tidal flexing heating a subsurface ocean with twice the volume of all Earth's oceans combined.",
            "score": 9.4,
            "keywords": [
                "europa",
                "jupiter moon",
                "alien life"
            ]
        },
        {
            "topic": "Voyager 1 Interstellar Journey",
            "title": "The 1970s Probe That Has Been Flying for 47 Years and Left the Solar System",
            "description": "The Golden Record, 8-track tape computers, and crossing the heliopause into interstellar space.",
            "score": 9.3,
            "keywords": [
                "voyager 1",
                "interstellar",
                "nasa"
            ]
        },
        {
            "topic": "Kilonova Neutron Star Collisions",
            "title": "Where All the Gold in the Universe Came From",
            "description": "R-process nucleosynthesis during catastrophic neutron star mergers forging heavy precious metals.",
            "score": 9.5,
            "keywords": [
                "kilonova",
                "gold origin",
                "neutron stars"
            ]
        },
        {
            "topic": "Dyson Sphere Megastructures",
            "title": "Could an Advanced Alien Civilization Enclose an Entire Star for Energy?",
            "description": "Freeman Dyson's concept of orbital collector swarms harnessing 380 septillion watts of stellar power.",
            "score": 9.3,
            "keywords": [
                "dyson sphere",
                "kardashev scale",
                "megastructures"
            ]
        },
        {
            "topic": "Martian Colonization Realities",
            "title": "The Lethal Challenges of Living on Mars (That Sci-Fi Never Shows)",
            "description": "Perchlorate toxic soil, solar radiation storms, low gravity bone loss, and atmospheric sublimations.",
            "score": 9.1,
            "keywords": [
                "mars colony",
                "space exploration",
                "spacex"
            ]
        },
        {
            "topic": "Cosmic Microwave Background",
            "title": "The 13.8-Billion-Year-Old Baby Picture of Our Universe",
            "description": "Recombination epoch radiation frozen into the sky at 2.7 Kelvin, confirming the Big Bang.",
            "score": 9.0,
            "keywords": [
                "cmb",
                "big bang",
                "cosmology"
            ]
        },
        {
            "topic": "Betelgeuse Supernova Countdown",
            "title": "The Monster Red Giant Star That Could Explode Any Day Now",
            "description": "Core carbon burning, recent dimming events, and what the sky will look like when Betelgeuse goes supernova.",
            "score": 9.4,
            "keywords": [
                "betelgeuse",
                "supernova",
                "astrophysics"
            ]
        },
        {
            "topic": "Oumuamua Interstellar Mystery",
            "title": "The Flat Cigar-Shaped Visitor From Another Solar System That Accelerated Away",
            "description": "Non-gravitational acceleration, lack of cometary outgassing, and Avi Loeb's solar sail hypothesis.",
            "score": 9.5,
            "keywords": [
                "oumuamua",
                "interstellar object",
                "space mystery"
            ]
        },
        {
            "topic": "Sagittarius A* Black Hole",
            "title": "Photographing the 4-Million-Sun Monster at the Center of the Milky Way",
            "description": "Event Horizon Telescope global interferometry imaging the glowing accretion ring of our galaxy's core.",
            "score": 9.3,
            "keywords": [
                "sagittarius a",
                "milky way",
                "black hole"
            ]
        },
        {
            "topic": "Saturn's Rings Lifespan",
            "title": "Saturn is Losing Its Rings: The Giant 'Ring Rain' Phenomenon",
            "description": "Cassini spacecraft data showing water ice draining into the planet at 10,000 kilograms per second.",
            "score": 9.0,
            "keywords": [
                "saturn rings",
                "cassini",
                "planetary science"
            ]
        },
        {
            "topic": "Venus Runaway Greenhouse",
            "title": "How Earth's Twin Planet Became a 900-Degree Acid-Raining Hellscape",
            "description": "Evaporating oceans, solar UV water stripping, and a 92-bar carbon dioxide atmosphere.",
            "score": 9.1,
            "keywords": [
                "venus",
                "runaway greenhouse",
                "solar system"
            ]
        },
        {
            "topic": "The Kuiper Belt and Oort Cloud",
            "title": "The Frozen Trillion-Comet Graveyard at the Edge of Our Solar System",
            "description": "Icy remnants of planetary formation stretching halfway to the nearest neighboring star.",
            "score": 8.9,
            "keywords": [
                "kuiper belt",
                "oort cloud",
                "comets"
            ]
        },
        {
            "topic": "Direct Dark Matter Detection",
            "title": "Hunting for WIMPs in Deep Underground Liquid Xenon Tanks",
            "description": "LZ and XENONnT detectors buried under miles of rock to block cosmic background noise.",
            "score": 9.1,
            "keywords": [
                "dark matter detection",
                "xenon",
                "particle astrophysics"
            ]
        },
        {
            "topic": "Gravitational Lensing",
            "title": "Einstein's Cosmic Telescopes: How Entire Galaxy Clusters Magnify Distant Light",
            "description": "Spacetime curvature magnifying primordial stars from the dawn of time into bright cosmic arcs.",
            "score": 9.2,
            "keywords": [
                "gravitational lensing",
                "einstein",
                "hubble"
            ]
        },
        {
            "topic": "The Great Attractor",
            "title": "The Mysterious Gravitational Anomaly Pulling Our Entire Galaxy Across Space",
            "description": "Millions of galaxies racing at 600 km/s toward a massive gravitational center hidden behind the Milky Way.",
            "score": 9.6,
            "keywords": [
                "great attractor",
                "cosmology",
                "galaxy movement"
            ]
        },
        {
            "topic": "The Wow! Signal Investigation",
            "title": "The 72-Second Radio Burst From Deep Space That Was Never Heard Again",
            "description": "Jerry Ehman's 1977 Big Ear discovery, 1420 MHz hydrogen line tuning, and modern cometary theories.",
            "score": 9.4,
            "keywords": [
                "wow signal",
                "seti",
                "radio astronomy"
            ]
        },
        {
            "topic": "The Carrington Event of 1859",
            "title": "The Super Solar Storm That Made Telegraph Wires Burst Into Flames",
            "description": "Coronal mass ejection triggering geomagnetic auroras visible in Cuba and the danger to modern power grids.",
            "score": 9.3,
            "keywords": [
                "carrington event",
                "solar storm",
                "space weather"
            ]
        },
        {
            "topic": "Jupiter's Great Red Spot",
            "title": "The 400-Year-Old Monster Storm Big Enough to Swallow Earth Whole",
            "description": "Anticyclonic circulation, 400mph winds, and Juno probe measurements showing it extends 300 miles deep.",
            "score": 9.1,
            "keywords": [
                "jupiter",
                "great red spot",
                "planetary science"
            ]
        },
        {
            "topic": "Exoplanet Atmosphere Spectroscopy",
            "title": "How We Detect Water and Methane on Alien Worlds 100 Light-Years Away",
            "description": "Starlight filtering through exoplanetary atmospheres during transits, creating unique absorption fingerprints.",
            "score": 9.2,
            "keywords": [
                "exoplanets",
                "spectroscopy",
                "transit method"
            ]
        },
        {
            "topic": "Rogue Planets in the Milky Way",
            "title": "Billions of Frozen Worlds Wandering the Dark Galaxy Without Any Star",
            "description": "Gravitational ejections during early planetary chaos creating rogue nomads heated by radioactive cores.",
            "score": 9.4,
            "keywords": [
                "rogue planets",
                "astronomy",
                "deep space"
            ]
        },
        {
            "topic": "The Ultimate Fate of the Universe",
            "title": "Heat Death, Big Rip, or Big Crunch: How Will Reality End?",
            "description": "Entropy maximization in 10^100 years: degenerate era, black hole evaporation, and the eternal dark vacuum.",
            "score": 9.5,
            "keywords": [
                "heat death",
                "big rip",
                "cosmology"
            ]
        },
        {
            "topic": "Giant Impact Hypothesis (Theia)",
            "title": "How a Mars-Sized Planet Smashed Into Earth to Create the Moon",
            "description": "Theia collision 4.5 billion years ago vaporizing Earth's mantle into an orbiting debris ring.",
            "score": 9.2,
            "keywords": [
                "moon origin",
                "theia",
                "planetary collision"
            ]
        },
        {
            "topic": "Relativistic Time Dilation Near Black Holes",
            "title": "Miller's Planet: Why 1 Hour Near Gargantua Equals 7 Years on Earth",
            "description": "Gravitational potential slowing the ticking rate of clocks in strong gravitational fields.",
            "score": 9.6,
            "keywords": [
                "time dilation",
                "interstellar physics",
                "black hole"
            ]
        },
        {
            "topic": "Pulsar Timing Arrays",
            "title": "Turning Natural Galactic Clocks Into a Giant Gravitational Wave Detector",
            "description": "Nanosecond precision of millisecond pulsars detecting ripples from supermassive black hole binaries.",
            "score": 9.1,
            "keywords": [
                "pulsar timing",
                "gravitational waves",
                "astronomy"
            ]
        },
        {
            "topic": "Red Dwarf Longevity",
            "title": "The Tiny Stars That Will Outlive the Big Bang by 10 Trillion Years",
            "description": "Full convective mixing burning hydrogen fuel so efficiently they will be the last burning stars in the cosmos.",
            "score": 9.0,
            "keywords": [
                "red dwarfs",
                "stellar evolution",
                "universe"
            ]
        },
        {
            "topic": "Titan's Liquid Methane Seas",
            "title": "The Only Other World With Clouds, Rain, Rivers, and Seas on Its Surface",
            "description": "Saturn's moon Titan: cryogenic liquid hydrocarbons, prebiotic chemistry, and the Dragonfly mission.",
            "score": 9.4,
            "keywords": [
                "titan",
                "saturn moon",
                "methane seas"
            ]
        },
        {
            "topic": "Kessler Syndrome Space Debris",
            "title": "How One Satellite Crash Could Trap Humanity on Earth Forever",
            "description": "Cascading orbital debris collisions wiping out low-Earth orbit telecommunications and satellites.",
            "score": 9.3,
            "keywords": [
                "kessler syndrome",
                "space debris",
                "space travel"
            ]
        },
        {
            "topic": "The Alcubierre Warp Drive",
            "title": "Can We Warp Spacetime to Travel Faster Than Light Without Breaking Relativity?",
            "description": "Contracting space ahead and expanding space behind using theoretical negative energy densities.",
            "score": 9.4,
            "keywords": [
                "warp drive",
                "alcubierre",
                "faster than light"
            ]
        },
        {
            "topic": "Asteroid Mining Economics",
            "title": "The $10,000-Quadrillion Asteroid That Holds More Wealth Than Earth's Entire GDP",
            "description": "16 Psyche: a core of pure nickel, iron, and platinum and the future of off-world mining.",
            "score": 9.3,
            "keywords": [
                "asteroid mining",
                "16 psyche",
                "space economy"
            ]
        },
        {
            "topic": "Lagrange Points Gravitational Balance",
            "title": "The Invisible Parking Spots in Space Where Gravity Pulls Cancel Out",
            "description": "L1 through L5 stable orbital pockets where JWST, SOHO, and Trojan asteroids hover effortlessly.",
            "score": 9.0,
            "keywords": [
                "lagrange points",
                "orbital mechanics",
                "jwst"
            ]
        },
        {
            "topic": "Earth's Magnetosphere Shield",
            "title": "What Protects Us From the Sun's Deadly 1-Million-MPH Radioactive Solar Wind",
            "description": "The Van Allen radiation belts deflecting charged coronal plasma to keep our atmosphere intact.",
            "score": 9.1,
            "keywords": [
                "magnetosphere",
                "solar wind",
                "space weather"
            ]
        },
        {
            "topic": "Fast Radio Bursts (FRBs)",
            "title": "Millisecond Cosmic Radio Blasts Outshining Entire Galaxies",
            "description": "Magnetar starquakes releasing incomprehensible energetic flare-ups across billions of light-years.",
            "score": 9.4,
            "keywords": [
                "fast radio bursts",
                "magnetars",
                "radio astronomy"
            ]
        },
        {
            "topic": "Interstellar Propulsion Tech",
            "title": "Antimatter Rockets, Fusion Drives, and Laser Light Sails to Reach Alpha Centauri",
            "description": "Breakthrough Starshot aiming to propel micro-chips to 20% light speed using 100-gigawatt lasers.",
            "score": 9.3,
            "keywords": [
                "interstellar propulsion",
                "solar sails",
                "space travel"
            ]
        },
        {
            "topic": "The Cosmic Web Structure",
            "title": "The Trillion-Light-Year Filament Network Connecting Every Galaxy",
            "description": "Dark matter bridges channeling cosmic gas into colossal galactic cluster intersections.",
            "score": 9.2,
            "keywords": [
                "cosmic web",
                "galaxies",
                "cosmology"
            ]
        },
        {
            "topic": "Stellar Nucleosynthesis",
            "title": "Carl Sagan: Why Every Atom in Your Body Was Forged Inside an Exploding Star",
            "description": "Hydrogen fusing to helium, carbon, oxygen, and silicon until iron chokes the stellar engine.",
            "score": 9.5,
            "keywords": [
                "nucleosynthesis",
                "stardust",
                "supernova"
            ]
        },
        {
            "topic": "Habitable Zones & Goldilocks Planets",
            "title": "The Precise Orbital Sweet Spot Where Liquid Water and Life Can Thrive",
            "description": "Stellar luminosity, atmospheric pressure, and greenhouse insulation defining habitable boundaries.",
            "score": 8.9,
            "keywords": [
                "goldilocks zone",
                "exoplanets",
                "astrobiology"
            ]
        },
        {
            "topic": "The Pioneer Anomaly Resolved",
            "title": "The Mysterious Force That Pulled NASA's Pioneer Probes Off Course",
            "description": "How 30 years of mysterious decelerations were finally explained by thermal recoil radiation pressure.",
            "score": 8.8,
            "keywords": [
                "pioneer anomaly",
                "nasa",
                "space physics"
            ]
        },
        {
            "topic": "Enceladus Cryovolcanic Geysers",
            "title": "Saturn's Moon Shooting Warm Salty Ocean Water Hundreds of Miles Into Space",
            "description": "Cassini flying through the plumes and detecting molecular hydrogen, methane, and organic compounds.",
            "score": 9.5,
            "keywords": [
                "enceladus",
                "cryovolcano",
                "astrobiology"
            ]
        },
        {
            "topic": "Andromeda-Milky Way Collision",
            "title": "What Will Happen in 4 Billion Years When Our Galaxy Crashes Into Andromeda?",
            "description": "The Milkomeda galactic merger: why almost zero individual stars will ever collide despite massive upheaval.",
            "score": 9.2,
            "keywords": [
                "andromeda collision",
                "milky way",
                "galaxy merger"
            ]
        },
        {
            "topic": "The Bo\u00f6tes Void (The Great Nothing)",
            "title": "A 330-Million-Light-Year Hole in the Universe With Almost No Galaxies",
            "description": "The creepiest cosmological void where you could look in every direction and see total pitch black.",
            "score": 9.6,
            "keywords": [
                "bootes void",
                "cosmic voids",
                "deep space"
            ]
        },
        {
            "topic": "Gamma-Ray Bursts (GRBs)",
            "title": "The Deadliest Explosions in the Universe That Could Sterilize Entire Planets",
            "description": "Collapsars and hypernovae firing relativistic particle jets packing more energy than the sun will emit in its lifetime.",
            "score": 9.5,
            "keywords": [
                "gamma ray bursts",
                "hypernova",
                "cosmic danger"
            ]
        },
        {
            "topic": "Planetary Nebulae Formation",
            "title": "The Death of Our Sun: The Ring and Helix Nebulae Glowing Shrouds",
            "description": "How medium-sized stars shed glowing ionized gas envelopes before leaving behind a white dwarf corpse.",
            "score": 8.9,
            "keywords": [
                "planetary nebulae",
                "sun death",
                "white dwarf"
            ]
        },
        {
            "topic": "Hawking Radiation & Black Hole Evaporation",
            "title": "How Quantum Virtual Particles Slowly Drain Black Holes Until They Explode",
            "description": "Particle-antiparticle pairs at the event horizon causing microscopic black holes to vanish in flashes.",
            "score": 9.3,
            "keywords": [
                "hawking radiation",
                "quantum field theory",
                "black holes"
            ]
        },
        {
            "topic": "The Twin Paradox of Relativity",
            "title": "Why the Astronaut Brother Returns Decades Younger Than His Twin",
            "description": "Special relativity acceleration asymmetry proving time is relative and non-absolute.",
            "score": 9.3,
            "keywords": [
                "twin paradox",
                "special relativity",
                "einstein"
            ]
        },
        {
            "topic": "Terraforming Mars Physics",
            "title": "Can We Melt Mars' Polar Caps to Create an Atmosphere Breathable for Humans?",
            "description": "Super-greenhouse gases, orbital magnetic dipole shields, and the fundamental lack of nitrogen.",
            "score": 9.2,
            "keywords": [
                "terraforming mars",
                "planetary engineering",
                "mars"
            ]
        },
        {
            "topic": "Technosignature Searches in SETI",
            "title": "Searching for Alien Lasers, Nuclear Waste, and Atmospheric Pollution",
            "description": "Beyond radio waves: detecting chlorofluorocarbons (CFCs), optical pulses, and orbital swarms.",
            "score": 9.3,
            "keywords": [
                "technosignatures",
                "seti",
                "alien detection"
            ]
        },
        {
            "topic": "Quasars: The Brightest Beacons in Reality",
            "title": "Supermassive Black Holes Shining 1,000 Times Brighter Than an Entire Galaxy",
            "description": "Accretion disks spinning at near-light speeds superheating matter into blinding relativistic beams.",
            "score": 9.4,
            "keywords": [
                "quasars",
                "accretion disk",
                "active galactic nuclei"
            ]
        },
        {
            "topic": "Cosmic Inflation & Multiverse",
            "title": "Did the Universe Expand Faster Than Light in the First Trillionth of a Second?",
            "description": "Alan Guth's inflation theory, quantum vacuum fluctuations, and the eternal multiverse hypothesis.",
            "score": 9.5,
            "keywords": [
                "cosmic inflation",
                "multiverse",
                "theoretical physics"
            ]
        }
    ]
}



class ContentIdeaGenerator:
    """Production content idea generation and recommendation engine.

    Orchestrates Gemini API requests with fallback to 400+ categorized evergreen
    templates, deduplicates against historical database records, and scores
    engagement potential based on curiosity mechanics and title framing.
    """

    SUPPORTED_NICHES: List[str] = [
        "science",
        "technology",
        "education",
        "history",
        "nature",
        "psychology",
        "health",
        "space",
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        rpm_limit: int = 15,
        default_niche: Optional[str] = None,
    ) -> None:
        """Initialize ContentIdeaGenerator.

        Args:
            api_key: Optional Gemini API key. Defaults to current_config.GEMINI_API_KEY.
            model_name: Optional Gemini model name. Defaults to 'gemini-1.5-flash'.
            rpm_limit: Maximum API requests per minute for rate limiting.
            default_niche: Fallback niche if none specified.
        """
        self.api_key = api_key or (current_config.GEMINI_API_KEY if current_config else "")
        self.model_name = model_name or (current_config.GEMINI_MODEL if current_config else "gemini-1.5-flash")
        self.default_niche = default_niche or (current_config.CHANNEL_NICHE if current_config else "science")
        self.rate_limiter = RateLimiter(rpm=rpm_limit)

        self._used_topic_hashes: Set[str] = set()
        self._topic_history: List[str] = []
        self._gemini_initialized = False

        self._init_gemini()
        self._load_database_history()

    def _init_gemini(self) -> None:
        """Configure Gemini client if API key and library are present."""
        if HAS_GENAI and self.api_key:
            try:
                genai.configure(api_key=self.api_key)
                self._gemini_initialized = True
                logger.info("Gemini API configured successfully with model %s", self.model_name)
            except Exception as exc:
                logger.warning("Failed to initialize Gemini API: %s. Using template fallback.", exc)
                self._gemini_initialized = False
        else:
            if not HAS_GENAI:
                logger.info("google-generativeai package not available. Using evergreen templates.")
            elif not self.api_key:
                logger.info("No Gemini API key supplied. Using evergreen templates.")

    def _load_database_history(self) -> None:
        """Preload historically used topics from the database to prevent duplicates."""
        if get_db is None or text is None:
            return

        try:
            with get_db() as session:
                try:
                    rows = session.execute(text("SELECT topic, title FROM content_ideas")).fetchall()
                    for r in rows:
                        if r[0]:
                            self._used_topic_hashes.add(self._normalize_topic(str(r[0])))
                            self._topic_history.append(str(r[0]))
                        if len(r) > 1 and r[1]:
                            self._used_topic_hashes.add(self._normalize_topic(str(r[1])))
                except Exception as exc:
                    logger.debug("Content ideas table query skipped: %s", exc)

                try:
                    rows_v = session.execute(text("SELECT topic, title FROM videos")).fetchall()
                    for r in rows_v:
                        if r[0]:
                            self._used_topic_hashes.add(self._normalize_topic(str(r[0])))
                            self._topic_history.append(str(r[0]))
                        if len(r) > 1 and r[1]:
                            self._used_topic_hashes.add(self._normalize_topic(str(r[1])))
                except Exception as exc:
                    logger.debug("Videos table query skipped: %s", exc)

            logger.info("Loaded %d previously used topics from database.", len(self._used_topic_hashes))
        except Exception as exc:
            logger.debug("Database history load skipped or encountered error: %s", exc)

    @staticmethod
    def _normalize_topic(text_in: str) -> str:
        """Normalize topic or title for fuzzy deduplication.

        Args:
            text_in: Topic text to normalize.

        Returns:
            str: Normalized lowercased string without punctuation.
        """
        if not text_in:
            return ""
        clean = re.sub(r"[^a-zA-Z0-9\s]", "", str(text_in).lower())
        return " ".join(clean.split())

    def _is_topic_used(self, topic: str, title: str = "") -> bool:
        """Check whether a topic or title was previously used.

        Args:
            topic: Topic identifier.
            title: Title text.

        Returns:
            bool: True if topic was used before, False otherwise.
        """
        norm_t = self._normalize_topic(topic)
        if norm_t in self._used_topic_hashes:
            return True

        if title:
            norm_title = self._normalize_topic(title)
            if norm_title in self._used_topic_hashes:
                return True

        return False

    def mark_topic_used(self, topic: str, title: str = "") -> bool:
        """Mark a topic and title as used in cache and database.

        Args:
            topic: Topic string.
            title: Title string.

        Returns:
            bool: True if successfully marked.
        """
        norm_t = self._normalize_topic(topic)
        self._used_topic_hashes.add(norm_t)
        self._topic_history.append(topic)

        if title:
            self._used_topic_hashes.add(self._normalize_topic(title))

        if get_db is not None and text is not None:
            try:
                with get_db() as session:
                    session.execute(
                        text("UPDATE content_ideas SET used = 1 WHERE topic = :topic OR title = :title"),
                        {"topic": topic, "title": title or topic},
                    )
            except Exception as exc:
                logger.warning("Could not mark topic '%s' as used in database: %s", topic, exc)

        return True

    def calculate_engagement_score(self, title: str, description: str, base_score: float = 8.0) -> float:
        """Score idea based on headline psychology and curiosity metrics.

        Heuristics:
        - Question framing ('Why', 'How', 'What if')
        - Curiosity trigger words ('Secret', 'Hidden', 'Truth', 'Paradox', 'Mystery')
        - Power numbers ('5', '7', '10', '1000')
        - Concise description with concrete hook

        Args:
            title: Headline to score.
            description: Summary description.
            base_score: Initial baseline score.

        Returns:
            float: Normalized score between 1.0 and 10.0.
        """
        score = base_score
        t_lower = title.lower()

        # Curiosity question openers (+0.5)
        if any(t_lower.startswith(q) for q in ("why ", "how ", "what happens ", "what if ", "could we ")):
            score += 0.5

        # Curiosity triggers (+0.4)
        curiosity_words = [
            "secret", "hidden", "truth", "paradox", "mystery", "forbidden",
            "unsolved", "shocking", "bizarre", "impossible", "breakthrough", "scandal"
        ]
        if any(w in t_lower for w in curiosity_words):
            score += 0.4

        # Specific numbers (+0.3)
        if re.search(r"\b(\d+|10x|100%|billion|trillion)\b", t_lower):
            score += 0.3

        # Title length sweet spot (35-70 characters)
        if 35 <= len(title) <= 70:
            score += 0.3
        elif len(title) > 95:
            score -= 0.4

        # Description substance
        if len(description.split()) >= 10:
            score += 0.2

        return max(1.0, min(10.0, round(score, 2)))

    def _generate_with_gemini(self, count: int, niche: str) -> List[ContentIdea]:
        """Generate original ideas using the Gemini API.

        Args:
            count: Number of ideas requested.
            niche: Target topic category.

        Returns:
            List[ContentIdea]: Generated structured ideas.
        """
        if not self._gemini_initialized or genai is None:
            return []

        self.rate_limiter.acquire()

        prompt = (
            f"You are a master YouTube content strategist and documentary writer specializing in high-retention "
            f"educational and curiosity-driven videos.\n\n"
            f"Generate {count * 2} unique, highly engaging, factually grounded YouTube video ideas for the niche: '{niche}'.\n\n"
            f"Requirements:\n"
            f"1. Every title must have a strong curiosity gap without being deceptive clickbait.\n"
            f"2. Focus on timeless evergreen topics that appeal to both casual viewers and enthusiasts.\n"
            f"3. Include an engaging 2-sentence description detailing the central mystery or explanation.\n"
            f"4. Output MUST be a valid JSON array of objects with the exact schema:\n"
            f"[\n"
            f"  {{\n"
            f'    "topic": "Concise subject name",\n'
            f'    "title": "Compelling YouTube video title (40-75 characters)",\n'
            f'    "description": "Engaging 2-sentence summary outlining the central mystery.",\n'
            f'    "niche": "{niche}",\n'
            f'    "estimated_score": 9.2,\n'
            f'    "keywords": ["tag1", "tag2", "tag3"]\n'
            f"  }}\n"
            f"]\n"
            f"Do not wrap in Markdown code blocks or return extra text. Return ONLY the JSON array."
        )

        try:
            model = genai.GenerativeModel(
                model_name=self.model_name,
                generation_config={"temperature": 0.7, "max_output_tokens": 2048},
            )
            response = model.generate_content(prompt)
            text_resp = response.text.strip()

            # Clean JSON if wrapped in markdown code fence
            if text_resp.startswith("```"):
                text_resp = re.sub(r"^```(?:json)?\n?", "", text_resp)
                text_resp = re.sub(r"\n?```$", "", text_resp).strip()

            parsed = safe_json_loads(text_resp, default=[])
            if not isinstance(parsed, list):
                logger.warning("Gemini did not return a JSON array: %s", text_resp[:200])
                return []

            results: List[ContentIdea] = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                topic = item.get("topic", "").strip()
                title = item.get("title", "").strip()
                desc = item.get("description", "").strip()

                if not topic or not title:
                    continue

                if self._is_topic_used(topic, title):
                    continue

                base_score = float(item.get("estimated_score", 8.5))
                final_score = self.calculate_engagement_score(title, desc, base_score)

                idea = ContentIdea(
                    topic=topic,
                    title=title,
                    description=desc,
                    niche=niche,
                    source="gemini",
                    score=final_score,
                    metadata={
                        "keywords": item.get("keywords", []),
                        "model": self.model_name,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                results.append(idea)
                if len(results) >= count:
                    break

            return results

        except Exception as exc:
            logger.warning("Error generating ideas with Gemini: %s. Falling back to templates.", exc)
            return []

    def _generate_from_templates(self, count: int, niche: str) -> List[ContentIdea]:
        """Generate ideas from the built-in evergreen template repository.

        Args:
            count: Number of ideas requested.
            niche: Target topic category.

        Returns:
            List[ContentIdea]: Selected and scored structured ideas.
        """
        category_pool = EVERGREEN_TEMPLATES.get(niche)
        if not category_pool:
            # Fallback to random supported niche if unknown
            niche = random.choice(self.SUPPORTED_NICHES)
            category_pool = EVERGREEN_TEMPLATES[niche]

        # Filter out already used templates
        unused = [
            t for t in category_pool
            if not self._is_topic_used(t["topic"], t["title"])
        ]

        # If all exhausted, reset local cache for this category to cycle
        if len(unused) < count:
            logger.info("Category '%s' exhausted unused templates. Recycling available templates.", niche)
            unused = list(category_pool)

        # Shuffle candidates to prevent deterministic repetition
        candidates = list(unused)
        random.shuffle(candidates)

        selected: List[ContentIdea] = []
        for item in candidates:
            topic = item["topic"]
            title = item["title"]
            desc = item["description"]
            base_score = float(item.get("score", 8.5))

            final_score = self.calculate_engagement_score(title, desc, base_score)

            idea = ContentIdea(
                topic=topic,
                title=title,
                description=desc,
                niche=niche,
                source="template",
                score=final_score,
                metadata={
                    "keywords": item.get("keywords", []),
                    "template_base_score": base_score,
                },
            )
            selected.append(idea)
            if len(selected) >= count:
                break

        return selected

    def _persist_ideas_safely(self, ideas: List[ContentIdea]) -> None:
        """Persist generated ideas to SQLite database if available.

        Args:
            ideas: List of ContentIdea objects to save.
        """
        if get_db is None or text is None or not ideas:
            return

        try:
            with get_db() as session:
                for idea in ideas:
                    session.execute(
                        text(
                            "INSERT INTO content_ideas "
                            "(topic, title, description, hook, niche, source, used, score, metadata_json, created_at) "
                            "VALUES (:topic, :title, :description, :hook, :niche, :source, :used, :score, :metadata_json, :created_at)"
                        ),
                        {
                            "topic": idea.topic,
                            "title": idea.title,
                            "description": idea.description,
                            "hook": idea.title,
                            "niche": idea.niche,
                            "source": idea.source,
                            "used": 1 if idea.used else 0,
                            "score": idea.score,
                            "metadata_json": json.dumps(idea.metadata),
                            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    )
        except Exception as exc:
            logger.debug("Idea persistence to database skipped or handled: %s", exc)

    def generate_ideas(self, count: int = 5, niche: Optional[str] = None) -> List[ContentIdea]:
        """Generate structured video ideas with deduplication and engagement scoring.

        Attempts Gemini API generation first if available, seamlessly backfilling
        or falling back to the 400+ evergreen topic templates when offline or rate-limited.

        Args:
            count: Number of unique ideas to return (1-50).
            niche: Optional category ('science', 'technology', 'education', 'history',
                   'nature', 'psychology', 'health', 'space'). If None, selects from
                   the default or cycles across niches.

        Returns:
            List[ContentIdea]: Structured ideas ready for production.
        """
        count = max(1, min(50, count))

        # Resolve niche
        target_niche = (niche or self.default_niche or "science").lower().strip()
        # Handle composite niches like 'science_technology_education'
        if target_niche not in self.SUPPORTED_NICHES:
            sub_matches = [s for s in self.SUPPORTED_NICHES if s in target_niche]
            target_niche = random.choice(sub_matches) if sub_matches else "science"

        ideas: List[ContentIdea] = []

        # Step 1: Attempt Gemini generation
        if self._gemini_initialized:
            gemini_ideas = self._generate_with_gemini(count=count, niche=target_niche)
            ideas.extend(gemini_ideas)

        # Step 2: Backfill from template bank if needed
        needed = count - len(ideas)
        if needed > 0:
            template_ideas = self._generate_from_templates(count=needed, niche=target_niche)
            ideas.extend(template_ideas)

        # Record generated topics in database if available
        self._persist_ideas_safely(ideas)

        # Sort descending by engagement score
        ideas.sort(key=lambda x: x.score, reverse=True)
        return ideas[:count]

    def get_trending_topics(self, niche: Optional[str] = None, count: int = 10) -> List[Dict[str, Any]]:
        """Return curated high-engagement trending topics (purely template-based, zero API calls).

        Provides viral potential ratings, seasonality indicators, and recommended
        content formats for immediate video production.

        Args:
            niche: Optional niche filter. If None, samples across all 8 niches.
            count: Total trending topics to return.

        Returns:
            List[Dict[str, Any]]: Curated trending topics with metadata.
        """
        count = max(1, min(50, count))

        target_niches = [niche.lower()] if (niche and niche.lower() in self.SUPPORTED_NICHES) else list(self.SUPPORTED_NICHES)

        all_candidates: List[Dict[str, Any]] = []
        for n in target_niches:
            for item in EVERGREEN_TEMPLATES.get(n, []):
                score = item.get("score", 8.0)
                # Assign virality level
                virality = "High" if score >= 9.3 else ("Medium-High" if score >= 8.8 else "Solid")
                rec_format = "short" if score >= 9.2 else ("long" if score < 8.9 else "both")

                all_candidates.append({
                    "topic": item["topic"],
                    "title": item["title"],
                    "description": item["description"],
                    "niche": n,
                    "trend_score": score,
                    "virality_potential": virality,
                    "recommended_format": rec_format,
                    "keywords": item.get("keywords", []),
                })

        # Sort by trend_score descending
        all_candidates.sort(key=lambda x: x["trend_score"], reverse=True)
        return all_candidates[:count]

    def get_used_topics_count(self) -> int:
        """Get the total count of deduplicated topics currently tracked.

        Returns:
            int: Number of tracked used topics.
        """
        return len(self._used_topic_hashes)

from typing import List, Dict, Optional
from dataclasses import dataclass
from shared.messages import ServerInfo, JobConfiguration, CapabilityMatch, EncoderType

@dataclass
class ServerScore:
    """Score d'évaluation d'un serveur pour un job"""
    server_id: str
    compatibility_score: float  # 0.0 - 1.0
    performance_score: float    # 0.0 - 1.0  
    load_score: float          # 0.0 - 1.0 (1.0 = pas chargé)
    total_score: float         # Score combiné
    missing_capabilities: List[str]
    warnings: List[str]

class CapabilityMatcher:
    """Moteur de correspondance capacités serveur/job"""
    
    def __init__(self):
        # Poids pour calcul score final
        self.weights = {
            'compatibility': 0.5,  # Encodeurs supportés
            'performance': 0.3,    # Performance brute
            'load': 0.2           # Charge actuelle
        }
        
        # Préférences encodeurs par performance
        self.encoder_preferences = {
            # Hardware encoders (plus rapides)
            'h264_nvenc': 1.0,
            'hevc_nvenc': 1.0,
            'h264_qsv': 0.9,
            'hevc_qsv': 0.9,
            'h264_videotoolbox': 0.95,
            'hevc_videotoolbox': 0.95,
            'h264_amf': 0.85,
            'hevc_amf': 0.85,
            
            # Software encoders (plus lents mais universels)
            'libx264': 0.7,
            'libx265': 0.6,
            'libvpx': 0.5,
            'libvpx-vp9': 0.45,
        }
    
    def find_best_servers(self, job: JobConfiguration, 
                         available_servers: List[ServerInfo],
                         max_results: int = 3) -> List[CapabilityMatch]:
        """Trouve les meilleurs serveurs pour un job donné"""
        
        scores = []
        
        for server in available_servers:
            if server.status != 'online':
                continue
                
            score = self._evaluate_server(job, server)
            scores.append(score)
        
        # Trier par score décroissant
        scores.sort(key=lambda x: x.total_score, reverse=True)
        
        # Convertir en CapabilityMatch
        matches = []
        for score in scores[:max_results]:
            match = CapabilityMatch(
                server_id=score.server_id,
                compatibility_score=score.compatibility_score,
                missing_capabilities=score.missing_capabilities,
                performance_estimate=score.performance_score,
                recommended=score.total_score > 0.7
            )
            matches.append(match)
        
        return matches
    
    def _evaluate_server(self, job: JobConfiguration, server: ServerInfo) -> ServerScore:
        """Évalue un serveur pour un job spécifique"""
        
        # 1. Score de compatibilité
        compatibility_score, missing_caps = self._calculate_compatibility(job, server)
        
        # 2. Score de performance
        performance_score = self._calculate_performance(job, server)
        
        # 3. Score de charge
        load_score = self._calculate_load_score(server)
        
        # 4. Score total pondéré
        total_score = (
            compatibility_score * self.weights['compatibility'] +
            performance_score * self.weights['performance'] +
            load_score * self.weights['load']
        )
        
        # 5. Génération warnings
        warnings = self._generate_warnings(job, server, compatibility_score)
        
        return ServerScore(
            server_id=server.server_id,
            compatibility_score=compatibility_score,
            performance_score=performance_score,
            load_score=load_score,
            total_score=total_score,
            missing_capabilities=missing_caps,
            warnings=warnings
        )
    
    def _calculate_compatibility(self, job: JobConfiguration, 
                               server: ServerInfo) -> tuple[float, List[str]]:
        """Calcule le score de compatibilité et liste les capacités manquantes"""
        
        required_encoders = [job.encoder] + job.required_capabilities
        available_encoders = (
            server.capabilities.software_encoders +
            [enc for encoders in server.capabilities.hardware_encoders.values() 
             for enc in encoders]
        )
        
        missing = []
        supported = []
        
        for encoder in required_encoders:
            if encoder in available_encoders:
                supported.append(encoder)
            else:
                missing.append(encoder)
        
        # Score basé sur ratio supporté/requis
        if not required_encoders:
            compatibility_score = 1.0
        else:
            compatibility_score = len(supported) / len(required_encoders)
        
        return compatibility_score, missing
    
    def _calculate_performance(self, job: JobConfiguration, server: ServerInfo) -> float:
        """Calcule le score de performance basé sur l'encodeur et le matériel"""
        
        # Score de base du serveur
        base_score = min(server.capabilities.estimated_performance / 1000.0, 1.0)
        
        # Bonus selon type d'encodeur
        encoder_bonus = self.encoder_preferences.get(job.encoder, 0.5)
        
        # Bonus selon type d'encodeur disponible
        has_hardware = any(
            job.encoder in encoders 
            for encoders in server.capabilities.hardware_encoders.values()
        )
        hardware_bonus = 1.3 if has_hardware else 1.0
        
        # Score final
        performance_score = min(base_score * encoder_bonus * hardware_bonus, 1.0)
        
        return performance_score
    
    def _calculate_load_score(self, server: ServerInfo) -> float:
        """Calcule le score de charge (1.0 = pas chargé, 0.0 = saturé)"""
        
        if server.max_jobs == 0:
            return 0.0
        
        job_load = server.current_jobs / server.max_jobs
        cpu_load = getattr(server.capabilities, 'current_load', 0.5)
        
        # Moyenne pondérée
        combined_load = (job_load * 0.7) + (cpu_load * 0.3)
        
        return max(0.0, 1.0 - combined_load)
    
    def _generate_warnings(self, job: JobConfiguration, server: ServerInfo, 
                          compatibility_score: float) -> List[str]:
        """Génère des avertissements pour l'utilisateur"""
        
        warnings = []
        
        if compatibility_score < 1.0:
            warnings.append(f"Encodeur {job.encoder} non supporté sur ce serveur")
        
        if server.current_jobs >= server.max_jobs:
            warnings.append("Serveur actuellement saturé")
        
        if hasattr(server.capabilities, 'current_load') and server.capabilities.current_load > 0.9:
            warnings.append("Charge CPU élevée sur ce serveur")
        
        # Vérifier taille fichier
        if job.file_size > server.capabilities.max_file_size_gb * 1024**3:
            warnings.append("Fichier trop volumineux pour ce serveur")
        
        # Vérifier résolution
        resolution_limits = {
            '1080p': 1920 * 1080,
            '2K': 2560 * 1440, 
            '4K': 3840 * 2160,
            '8K': 7680 * 4320
        }
        
        job_pixels = self._parse_resolution(job.resolution)
        max_pixels = resolution_limits.get(server.capabilities.max_resolution, 1920*1080)
        
        if job_pixels > max_pixels:
            warnings.append(f"Résolution {job.resolution} peut être trop élevée")
        
        return warnings
    
    def _parse_resolution(self, resolution: str) -> int:
        """Parse une résolution type '1920x1080' en nombre de pixels"""
        try:
            if 'x' in resolution:
                w, h = resolution.split('x')
                return int(w) * int(h)
        except:
            pass
        return 1920 * 1080  # Fallback
    
    def suggest_alternatives(self, job: JobConfiguration, 
                           available_servers: List[ServerInfo]) -> List[str]:
        """Suggère des alternatives si aucun serveur compatible"""
        
        suggestions = []
        
        # Rechercher encodeurs similaires disponibles
        similar_encoders = {
            'h264_nvenc': ['libx264', 'h264_qsv', 'h264_videotoolbox'],
            'hevc_nvenc': ['libx265', 'hevc_qsv', 'hevc_videotoolbox'],
            'h264_videotoolbox': ['libx264', 'h264_nvenc', 'h264_qsv'],
            'libx264': ['h264_nvenc', 'h264_qsv', 'h264_videotoolbox']
        }
        
        alternatives = similar_encoders.get(job.encoder, [])
        
        for server in available_servers:
            available = (
                server.capabilities.software_encoders +
                [enc for encoders in server.capabilities.hardware_encoders.values() 
                 for enc in encoders]
            )
            
            for alt in alternatives:
                if alt in available:
                    suggestions.append(
                        f"Serveur {server.name} supporte {alt} (alternative à {job.encoder})"
                    )
                    break
        
        return suggestions[:3]  # Top 3 suggestions

"""
Profile aggregation service.
Aggregates user profile data from all past interview call analyses.
"""

import logging

logger = logging.getLogger(__name__)


class ProfileAggregator:
	"""Service for aggregating user profile data from call analyses."""
	
	def __init__(self, supabase_client):
		"""
		Initialize the profile aggregator.
		
		Args:
			supabase_client: Initialized Supabase client
		"""
		self.supabase = supabase_client
	
	def aggregate_user_profile(self, session_id: str) -> dict:
		"""
		Aggregate user profile data from all interview call analyses for the user.
		
		Reads the interview_call_analysis table for all records from the user who made the call.
		Extracts personality, myers-briggs score, and expertise description fields,
		combines them into one string per record, and returns the aggregated data.
		
		Args:
			session_id: The LiveKit session ID to identify the user
			
		Returns:
			dict: Processing result with status and aggregated data
			
		Raises:
			ValueError: If session_id is invalid or call not found
			Exception: If processing fails
		"""
		if not session_id:
			raise ValueError("session_id is required")
		
		if not self.supabase:
			raise Exception("Supabase client not initialized")
		
		logger.info("=" * 60)
		logger.info(f"👤 AGGREGATING USER PROFILE FOR SESSION: {session_id}")
		logger.info("=" * 60)
		
		# First, get the user_id from the session
		user_id = self._get_user_id_from_session(session_id)
		
		if not user_id:
			logger.error(f"❌ Could not find user_id for session: {session_id}")
			raise ValueError(f"User not found for session_id: {session_id}")
		
		logger.info(f"Found user_id: {user_id}")
		
		# Fetch all call analyses for this user
		response = self.supabase.table("interview_call_analysis")\
			.select("personality, myers_briggs_code, expertise_description, session_id, created_at")\
			.eq("user_id", user_id)\
			.order("created_at", desc=False)\
			.execute()
		
		if not response.data or len(response.data) == 0:
			logger.warning(f"⚠️ No call analyses found for user_id: {user_id}")
			return {
				"status": "success",
				"message": "No call analyses found for user",
				"session_id": session_id,
				"user_id": user_id,
				"record_count": 0,
				"aggregated_profiles": []
			}
		
		analyses = response.data
		logger.info(f"Found {len(analyses)} call analysis record(s) for user")
		
		# Aggregate profile data
		aggregated_profiles = []
		
		logger.info("\n" + "=" * 60)
		logger.info("📊 AGGREGATED USER PROFILE DATA")
		logger.info("=" * 60 + "\n")
		
		for idx, analysis in enumerate(analyses, 1):
			personality = analysis.get('personality', 'N/A')
			myers_briggs = analysis.get('myers_briggs_code', 'N/A')
			expertise = analysis.get('expertise_description', 'N/A')
			analysis_session = analysis.get('session_id', 'N/A')
			created_at = analysis.get('created_at', 'N/A')
			
			# Combine fields into one string
			combined_profile = (
				f"Session: {analysis_session}\n"
				f"Created: {created_at}\n"
				f"Myers-Briggs: {myers_briggs}\n"
				f"Personality: {personality}\n"
				f"Expertise: {expertise}"
			)
			
			aggregated_profiles.append({
				"session_id": analysis_session,
				"created_at": created_at,
				"myers_briggs_code": myers_briggs,
				"personality": personality,
				"expertise_description": expertise,
				"combined_profile": combined_profile
			})
			
			# Print to terminal
			logger.info(f"Record {idx}/{len(analyses)}:")
			logger.info("-" * 60)
			logger.info(combined_profile)
			logger.info("-" * 60 + "\n")
		
		logger.info("=" * 60)
		logger.info("✅ PROFILE AGGREGATION COMPLETE")
		logger.info(f"Processed {len(aggregated_profiles)} record(s)")
		logger.info("=" * 60)
		
		return {
			"status": "success",
			"message": "Profile data aggregated successfully",
			"session_id": session_id,
			"user_id": user_id,
			"record_count": len(aggregated_profiles),
			"aggregated_profiles": aggregated_profiles
		}
	
	def _get_user_id_from_session(self, session_id: str) -> str:
		"""
		Get the user_id associated with a session.
		
		Args:
			session_id: The LiveKit session ID
			
		Returns:
			str: The user_id, or None if not found
		"""
		# Try interview_call_analysis first (most recent table)
		response = self.supabase.table("interview_call_analysis")\
			.select("user_id")\
			.eq("session_id", session_id)\
			.limit(1)\
			.execute()
		
		if response.data and len(response.data) > 0:
			return response.data[0].get('user_id')
		
		# Fallback to interview_calls table
		response = self.supabase.table("interview_calls")\
			.select("user_id")\
			.eq("session_id", session_id)\
			.limit(1)\
			.execute()
		
		if response.data and len(response.data) > 0:
			return response.data[0].get('user_id')
		
		return None


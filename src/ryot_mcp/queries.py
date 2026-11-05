"""The GraphQL documents this server sends, each a named operation.

Written against Ryot v10's schema (the `metadata*` / `userMetadata*` /
`collection*` families). Ryot's v11 is a rewrite with a new API; when it
lands, this file and the shapes service.py reads are what change."""

from __future__ import annotations

USER_DETAILS = """
query UserDetails {
  userDetails {
    __typename
    ... on UserDetails { id name preferences { general { reviewScale } } }
    ... on UserDetailsError { error }
  }
}
"""

GENERATE_AUTH_TOKEN = """
mutation GenerateAuthToken { generateAuthToken }
"""

CORE_DETAILS = """
query CoreDetails {
  coreDetails { version metadataLotSourceMappings { lot sources } }
}
"""

METADATA_SEARCH = """
query MetadataSearch($input: MetadataSearchInput!) {
  metadataSearch(input: $input) { response { items details { totalItems nextPage } } }
}
"""

METADATA_DETAILS = """
query MetadataDetails($metadataId: String!) {
  metadataDetails(metadataId: $metadataId) {
    response {
      id title lot source identifier isPartial publishYear publishDate description
      providerRating productionStatus originalLanguage sourceUrl suggestions
      genres { name }
      watchProviders { name }
      creators { name items { idOrName isFree character } }
      showSpecifics { runtime totalSeasons totalEpisodes seasons { seasonNumber name episodes { episodeNumber } } }
      movieSpecifics { runtime }
      animeSpecifics { episodes }
      mangaSpecifics { chapters volumes }
      bookSpecifics { pages }
    }
  }
}
"""

SEEN_FIELDS = """
id state progress startedOn finishedOn lastUpdatedOn providersConsumedOn
showExtraInformation { season episode }
animeExtraInformation { episode }
mangaExtraInformation { chapter volume }
"""

USER_METADATA_DETAILS = f"""
query UserMetadataDetails($metadataId: String!) {{
  userMetadataDetails(metadataId: $metadataId) {{
    response {{
      seenByUserCount averageRating mediaReason
      inProgress {{ {SEEN_FIELDS} }}
      history {{ {SEEN_FIELDS} }}
      nextEntry {{ season episode volume chapter }}
      reviews {{
        id rating textOriginal isSpoiler visibility postedOn
        postedBy {{ id }}
        showExtraInformation {{ season episode }}
      }}
      collections {{ details {{ collectionName }} }}
    }}
  }}
}}
"""

PERSON_NAME = """
query PersonDetails($personId: String!) {
  personDetails(personId: $personId) { response { details { name } } }
}
"""

COLLECTIONS = """
query UserCollectionsList {
  userCollectionsList { response { id name count isDefault description } }
}
"""

COLLECTION_CONTENTS = """
query CollectionContents($input: CollectionContentsInput!) {
  collectionContents(input: $input) {
    response {
      totalItems
      details { id name description }
      results { items { entityId entityLot } details { totalItems nextPage } }
    }
  }
}
"""

ADD_TO_COLLECTION = """
mutation DeployAddEntitiesToCollectionJob($input: ChangeCollectionToEntitiesInput!) {
  deployAddEntitiesToCollectionJob(input: $input)
}
"""

REMOVE_FROM_COLLECTION = """
mutation DeployRemoveEntitiesFromCollectionJob($input: ChangeCollectionToEntitiesInput!) {
  deployRemoveEntitiesFromCollectionJob(input: $input)
}
"""

CREATE_COLLECTION = """
mutation CreateOrUpdateCollection($input: CreateOrUpdateCollectionInput!) {
  createOrUpdateCollection(input: $input) { id }
}
"""

USER_METADATA_LIST = """
query UserMetadataList($input: UserMetadataListInput!) {
  userMetadataList(input: $input) { response { items details { totalItems nextPage } } }
}
"""

UPCOMING = """
query UserUpcomingCalendarEvents($input: UserUpcomingCalendarEventInput!) {
  userUpcomingCalendarEvents(input: $input) {
    date metadataId
    showExtraInformation { season episode }
    animeExtraInformation { episode }
  }
}
"""

UPDATE_MEDIA_ENTITY = """
mutation DeployUpdateMediaEntityJob($input: EntityWithLotInput!) {
  deployUpdateMediaEntityJob(input: $input)
}
"""

TRENDING = """
query TrendingMetadata { trendingMetadata }
"""

PROGRESS_UPDATE = """
mutation DeployBulkMetadataProgressUpdate($input: [MetadataProgressUpdateInput!]!) {
  deployBulkMetadataProgressUpdate(input: $input)
}
"""

DELETE_SEEN = """
mutation DeleteSeenItem($seenId: String!) { deleteSeenItem(seenId: $seenId) { id } }
"""

REVIEW = """
mutation CreateOrUpdateReview($input: CreateOrUpdateReviewInput!) { createOrUpdateReview(input: $input) { id } }
"""

DELETE_REVIEW = """
mutation DeleteReview($reviewId: String!) { deleteReview(reviewId: $reviewId) }
"""

USER_INTEGRATIONS = """
query UserIntegrations { userIntegrations { id name provider isDisabled } }
"""

CREATE_OR_UPDATE_INTEGRATION = """
mutation CreateOrUpdateUserIntegration($input: CreateOrUpdateUserIntegrationInput!) {
  createOrUpdateUserIntegration(input: $input)
}
"""

NOTIFICATION_PLATFORMS = """
query UserNotificationPlatforms { userNotificationPlatforms { id lot description isDisabled } }
"""

CREATE_NOTIFICATION_PLATFORM = """
mutation CreateUserNotificationPlatform($input: CreateUserNotificationPlatformInput!) {
  createUserNotificationPlatform(input: $input)
}
"""
